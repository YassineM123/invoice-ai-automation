# French Invoice / Devis AI Automation

Système simple pour créer ou corriger automatiquement des devis et factures français.

## Fonctionnalités

- Upload PDF, image ou texte
- Extraction IA avec OpenAI
- Calcul automatique HT / TVA / TTC
- Détection des erreurs
- Rapport de correction JSON
- Génération PDF avec WeasyPrint
- Génération DOCX avec python-docx
- WhatsApp webhook optionnel
- Docker + tests unitaires

Le système ne doit jamais inventer les données légales ou financières. Toute valeur incertaine est marquée `à confirmer`.

## Installation locale

```bash
cp .env.example .env
# Ajouter OPENAI_API_KEY dans .env
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
uvicorn app.main:app --reload
```

Windows PowerShell:

```powershell
cp .env.example .env
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
uvicorn app.main:app --reload
```

## Docker

```bash
cp .env.example .env
docker compose up --build
```

API only:

```bash
docker compose up --build api
```

WhatsApp bot with API:

```bash
docker compose up --build api whatsapp-bot
```

WhatsApp bot only, when the API container is already running:

```bash
docker compose up --build whatsapp-bot
```

The Compose setup stores runtime files in `./runtime` on your machine:

- `./runtime/outputs`: generated PDF, DOCX and reports
- `./runtime/uploads`: uploaded or temporary inputs
- `./runtime/whatsapp-session`: persistent WhatsApp Web browser login
- `./runtime/whatsapp-downloads`: media downloaded from WhatsApp
- `./runtime/whatsapp-generated`: files downloaded by the bot before sending back

This local setup does not require paid hosting. For personal use, the only external paid dependency is your OpenAI API usage.

## Test API

Upload fichier:

```bash
curl -F "file=@examples/sample_devis.txt" http://localhost:8000/documents/correct
```

Création depuis texte:

```bash
curl -X POST http://localhost:8000/documents/create-from-text   -F "text=Facture pour Ahmed Ben Ali, chantier 15 rue Victor Hugo Paris, peinture appartement, prix HT 950, TVA 10%"
```

## Tests

```bash
pytest
```

## Configuration entreprise

Modifier `app/company_profile.json` une seule fois:

```json
{
  "name": "ZEN Groupe",
  "address": "...",
  "siret": "...",
  "vat_number": "...",
  "email": "...",
  "phone": "...",
  "iban": "...",
  "insurance_decennale": "..."
}
```

Ces données sont ensuite injectées automatiquement dans tous les documents.

## Telegram Bot (Recommandé / اسهل واسرع)

Le bot Telegram est beaucoup plus stable et simple que WhatsApp Web : aucune session navigateur Playwright, aucun QR code à scanner, et utilisation de l'API officielle gratuite de Telegram.

### Configuration Telegram

1. Ouvrez Telegram et cherchez `@BotFather`.
2. Envoyez `/newbot`, choisissez un nom et un username (ex: `my_invoice_bot`).
3. Copiez le token reçu (ex: `123456789:ABCdefGh...`).
4. Ajoutez-le dans votre fichier `.env` :

```env
TELEGRAM_BOT_TOKEN="123456789:ABCdefGh..."
# Optionnel: limitez l'accès à votre ID Telegram uniquement (séparés par des virgules)
TELEGRAM_ALLOWED_CHAT_IDS=""
```

### Lancement du Bot Telegram

En local :

```bash
# Terminal 1 : lancer l'API
uvicorn app.main:app --reload

# Terminal 2 : lancer le bot Telegram
python -m app.bot.telegram_bot
```

Avec Docker :

```bash
docker compose up --build api telegram-bot
```

### Utilisation dans Telegram

- Envoyez `/start` pour démarrer.
- Envoyez les détails de la prestation en texte libre (ex: *"Facture pour Jean Dupont, 12 rue de la Paix Paris, rénovation électrique 1200€ HT, TVA 10%"*).
- Ou envoyez directement un document (PDF, image, texte) pour le faire corriger.
- Le bot vous envoie directement les fichiers générés (**PDF**, **DOCX**, **rapport JSON**) dans la conversation !

## Process WhatsApp

1. Client envoie `Bonjour`
2. Bot explique les infos nécessaires
3. Client envoie les données en texte libre
4. Le système génère PDF + DOCX
5. Le bot renvoie les liens

## WhatsApp Web bot personnel

Le bot Playwright est separe du webhook WhatsApp Cloud API. Il ouvre WhatsApp Web dans un profil persistant et ne lit/repond qu'a un seul chat autorise.

Configuration `.env`:

```env
WHATSAPP_WEB_ENABLED=true
WHATSAPP_ALLOWED_CHAT_NAME="Nom exact du chat"
WHATSAPP_ALLOWED_PHONE=""
WHATSAPP_SESSION_DIR="./runtime/whatsapp-session"
WHATSAPP_DOWNLOAD_DIR="./runtime/whatsapp-downloads"
WHATSAPP_ALLOW_GROUPS=false
WHATSAPP_POLL_INTERVAL_SECONDS=3
WHATSAPP_HEADLESS=false
WHATSAPP_QR_SCREENSHOT_PATH="./runtime/whatsapp-qr-login.png"
API_BASE_URL="http://localhost:8000"
```

Demarrage local sans Docker:

```bash
uvicorn app.main:app --reload
python -m app.bot.whatsapp_web_bot
```

Pour limiter le bot a un seul chat, renseigner au moins une de ces variables avant le demarrage:

```env
WHATSAPP_ALLOWED_CHAT_NAME="Nom exact affiche dans WhatsApp"
WHATSAPP_ALLOWED_PHONE="+33600000000"
```

Le bot refuse de lire ou repondre si le chat ouvert ne correspond pas a ce nom ou telephone.
Si les deux variables sont renseignees, les deux doivent correspondre au chat ouvert.

Scanner le QR code:

- Sans Docker, Chromium s'ouvre localement: scanner le QR code affiche dans la fenetre.
- Avec Docker, le bot tourne generalement en mode headless et enregistre une capture dans `./runtime/whatsapp-qr-login.png`. Ouvrir cette image sur la machine hote et scanner le QR code avec WhatsApp mobile.
- La session est sauvegardee dans `WHATSAPP_SESSION_DIR`; les prochains demarrages ne redemandent pas le QR tant que la session reste valide.

Arret propre:

```bash
docker compose stop whatsapp-bot
docker compose stop api
```

Ou utiliser `Ctrl+C` si Compose tourne au premier plan. Ne supprimez pas `./runtime/whatsapp-session` sauf si vous voulez forcer une nouvelle connexion WhatsApp Web.

Commandes supportees dans le chat autorise:

- `/start`: explique ce que le bot peut faire
- `/help`: liste les informations utiles pour facture/devis
- `/reset`: efface l'etat de conversation courant
- `/status`: verifie si le bot et l'API sont prets

Logique de conversation:

- Le bot garde une session avec les etapes `waiting_for_document_type`, `collecting_client_info`, `collecting_items`, `confirming_summary`, `generating_document`, `completed`.
- Il demande uniquement les donnees variables manquantes: type de document, client, chantier, prestations, quantite, prix HT, et options comme TVA/remise/acompte/paiement seulement si elles sont mentionnees.
- Les donnees entreprise viennent toujours de `app/company_profile.json`: nom, logo, adresse, SIRET/SIREN, TVA, contact, IBAN, assurance, conditions de paiement et pied legal.
- Avant generation, le bot envoie: `Voici le resume avant generation...` puis attend `OUI` ou une correction.
- Toute donnee incertaine est normalisee en `a confirmer` / `à confirmer` dans la session et transmise au pipeline sans invention.

Regles de securite:

- Le bot verifie le nom ou telephone du chat avant toute lecture/reponse.
- Les autres conversations sont ignorees et seulement journalisees.
- Aucun envoi en masse n'est implemente.
- Les groupes sont bloques sauf si `WHATSAPP_ALLOW_GROUPS=true`.
- Les documents generes par l'API sont telecharges puis renvoyes uniquement au chat autorise.

Limite importante: ce bot utilise l'automatisation de WhatsApp Web avec Playwright pour un MVP personnel/local. WhatsApp peut changer son interface a tout moment, ce qui peut casser les selecteurs du bot. Pour un produit public ou durable, utilisez une integration officielle et une validation humaine adaptee.

## Important production

Pour une vraie mise en production:

- Stocker clients/documents dans PostgreSQL
- Protéger les URLs de fichiers avec authentification
- Ajouter une interface admin
- Ajouter une validation humaine avant envoi définitif
- Ajouter stockage S3 ou Cloudflare R2
- Ajouter logs et monitoring
