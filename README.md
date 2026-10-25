# 🎯 Radar de stages — Luxembourg

Assistant personnel qui surveille les offres de stage au Luxembourg, les classe selon ton profil
(score /100 expliqué), t'alerte sur Telegram dès qu'une offre pertinente apparaît, et te permet de
suivre tes candidatures dans un tableau de bord web (ordinateur + téléphone).

**Coût : 0 €.** Tout tourne sur des offres gratuites, ordinateur éteint.

| Brique | Service (gratuit) | Rôle |
|---|---|---|
| Robot de collecte | GitHub Actions (dépôt public) | passe toutes les ~20 min, récap à 7h |
| Base de données | Supabase (Postgres) | offres, candidatures, paramètres, historique |
| Alertes | Bot Telegram | push instantané sur téléphone + ordinateur |
| Tableau de bord | GitHub Pages | site web protégé par mot de passe |
| Alertes e-mail (option) | Boîte Gmail dédiée | lit les alertes LinkedIn / jobs.lu / Indeed / JobTeaser |

---

## Mise en service (≈ 30 min, une seule fois)

> Tu dois créer toi-même les comptes (GitHub, Supabase, Telegram) : personne ne peut le faire à ta place.
> Garde un bloc-notes ouvert pour coller les 6 valeurs ci-dessous au fur et à mesure.

### 1. Supabase (la base de données)
1. Crée un compte sur **supabase.com** (connexion avec GitHub possible, pas de carte bancaire).
2. **New project** → nom `stage-radar`, mot de passe au choix, région **Frankfurt (eu-central-1)**.
3. Menu **SQL Editor** → **New query** → colle tout le contenu de `supabase/schema.sql` → **Run**.
   Tu dois voir « Success. No rows returned ».
4. Menu **Authentication → Users → Add user → Create new user** : ton e-mail + un mot de passe,
   coche **Auto Confirm User**. C'est ton identifiant pour le tableau de bord.
5. **Authentication → Sign In / Providers** : désactive **Allow new users to sign up**
   (personne d'autre ne pourra créer de compte).
6. **Project Settings → API** (ou *Data API* / *API Keys*) : note
   - `Project URL` → **SUPABASE_URL**
   - clé `anon` / `public` → **SUPABASE_ANON_KEY**
   - clé `service_role` (secrète !) → **SUPABASE_SERVICE_KEY**

### 2. Telegram (les alertes)
1. Dans Telegram, ouvre **@BotFather** → `/newbot` → choisis un nom (ex. *Radar Victor*) et un
   identifiant finissant par `bot`. Il te donne un token → **TELEGRAM_BOT_TOKEN**.
2. Ouvre ton nouveau bot et envoie-lui `/start`.
3. Dans un navigateur : `https://api.telegram.org/bot<TOKEN>/getUpdates` (remplace `<TOKEN>`).
   Repère `"chat":{"id": 123456789` → **TELEGRAM_CHAT_ID**.

### 3. GitHub (le robot et le site)
1. Crée un compte sur **github.com** puis **New repository** → nom `stage-radar`, **Public**
   (indispensable pour que le robot et le site soient gratuits ; le code est visible, tes données
   non : elles sont dans Supabase, protégées par ton mot de passe).
2. **Add file → Upload files** : glisse **tout le contenu** du dossier `stage-radar`
   (sur Mac, le dossier `.github` est caché : `Cmd + Maj + .` dans le Finder pour l'afficher).
   **Commit changes**.
3. **Settings → Secrets and variables → Actions**
   - onglet **Secrets** → *New repository secret*, un par un :
     `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
     `BACKUP_KEY` (une longue phrase de ton choix ; garde-la, elle déchiffre les sauvegardes)
   - onglet **Variables** → *New repository variable* :
     `SUPABASE_URL`, `SUPABASE_ANON_KEY`,
     `DASHBOARD_URL` = `https://<ton-identifiant-github>.github.io/stage-radar`
4. **Settings → Pages** → *Source* : **GitHub Actions**.
5. **Actions** → si GitHub le demande, clique *I understand… enable workflows*.
6. **Actions → Publier le tableau de bord → Run workflow**. Au bout d'1 min, ouvre `DASHBOARD_URL`
   et connecte-toi avec l'utilisateur créé à l'étape 1.4.

### 4. Premier lancement
1. **Actions → Radar → Run workflow** → mode **test-notify** → tu reçois 2 messages Telegram
   (« les notifications fonctionnent » + une offre d'exemple).
2. **Actions → Radar → Run workflow** → mode **run** → premier import : un seul message
   « Radar activé » avec le top 15 des stages déjà en ligne (pas 150 alertes d'un coup).
3. Ensuite tout est automatique : chaque nouvelle offre pertinente arrive sur Telegram,
   et le récap arrive chaque matin vers 7h.

### 5. (Recommandé) Alertes e-mail : LinkedIn, jobs.lu, Indeed, JobTeaser
LinkedIn et Indeed interdisent la lecture automatique de leurs pages, mais envoient des alertes
e-mail. Le radar les lit dans une boîte dédiée :
1. Crée une adresse Gmail dédiée (ex. `victor.radar.stages@gmail.com`), active la **validation en
   2 étapes**, puis crée un **mot de passe d'application** (myaccount.google.com → Sécurité →
   Mots de passe des applications).
2. Avec cette adresse, crée des alertes (fréquence la plus élevée possible) :
   - **LinkedIn Jobs** : « intern », « internship », « stage », « trainee » + lieu *Luxembourg*
   - **jobs.lu**, **lu.indeed.com** : mêmes mots-clés
   - **JobTeaser (SKEMA)** : alertes stage Luxembourg ; ou transfère-les automatiquement depuis ta
     boîte école vers l'adresse dédiée
3. Secrets GitHub : `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` (et `DIGEST_EMAIL_TO` si tu veux aussi le
   récap par e-mail).
4. Tableau de bord → **Sources** → coche **Alertes e-mail**.

---

## Utilisation au quotidien
- **Telegram** : bouton *Voir l'offre* (annonce originale) et *Tableau de bord* (fiche + suivi).
  Les offres « Prioritaires » sonnent ; les « Intéressantes » arrivent en silencieux.
  Pas d'alerte entre 23h et 7h (réglable) : elles arrivent à 7h.
- **Fiche d'une offre** : statut, notes, date de candidature, prochaine action (+ export calendrier),
  avis 🎯 / 👍 / 👎 qui ajuste le score des futures offres de la même entreprise, détail du score,
  et recherches préparées pour trouver des contacts (alumni SKEMA/ICN, RH, équipe).
- **Candidature envoyée** → une relance est programmée automatiquement à J+10.
- **Paramètres** : seuils, mots-clés, poids, entreprises cibles, statuts. Les scores sont
  recalculés au passage suivant.
- **Sources** : état de chaque source, fréquence, activer / désactiver, en ajouter une nouvelle.

## Ajouter une entreprise (site Workday)
Beaucoup de banques et sociétés de gestion utilisent Workday : l'URL de leur site carrière ressemble à
`https://<tenant>.wd3.myworkdayjobs.com/<site>`. Tableau de bord → Sources → *Ajouter une source*,
type `workday`, puis vérifie-la : **Actions → Radar → Run workflow → mode check-source**, source = son identifiant.

---

## Ce qui est surveillé (et les limites)
| Source | Méthode | Fréquence | Limite |
|---|---|---|---|
| Moovijob — stages (+ Graduate/V.I.E.) | pages publiques (autorisées par leur robots.txt) | 20 min | dépend de leur mise en page : alerte si elle change |
| Deutsche Bank / DWS, Lombard Odier, PGIM, Wellington | API publique Workday | 30–60 min | une entrée par entreprise |
| Candriam, Quintet | pages carrière HTML | 60 min | dépend de leur mise en page |
| LinkedIn, jobs.lu, Indeed, JobTeaser… | alertes e-mail | 15 min | vitesse = fréquence d'envoi de l'alerte |

- **Pas instantané** : GitHub lance le robot toutes les ~20 min et peut le décaler de quelques minutes
  aux heures chargées. Délai réaliste : 20 à 40 min après publication sur une source surveillée.
- **Pas exhaustif** : une entreprise qui ne publie que sur son propre site non surveillé ne sera pas
  vue, sauf si elle passe par Moovijob, LinkedIn ou une autre alerte. Ajoute-la en source.
- **Expiration** : une offre est marquée expirée quand *toutes* les sources qui l'affichaient l'ont
  retirée pendant 2 passages consécutifs (les alertes e-mail ne servent pas à détecter l'expiration).
- **Supabase gratuit** met en pause un projet inactif une semaine ; le robot l'utilise toutes les
  20 min, donc ça n'arrive pas tant qu'il tourne.
- **GitHub** suspend les tâches planifiées d'un dépôt public sans activité depuis 60 jours : le
  workflow *Maintenance* fait un petit commit mensuel pour l'éviter.

## Sauvegardes
Chaque dimanche, *Maintenance* exporte toute la base, chiffrée avec `BACKUP_KEY`, dans les artefacts
GitHub (90 jours). Restauration : télécharge le fichier `.enc` puis, en local,
`BACKUP_KEY=… SUPABASE_URL=… SUPABASE_SERVICE_KEY=… python -m radar restore fichier.enc`.

## Pour développer
```bash
pip install -r requirements.txt pytest
python -m pytest -q                                   # tests (sans réseau)
python -m radar check-source moovijob-stages --no-db  # teste une source sans base
```
Structure : `radar/connectors/` (une classe par type de source), `radar/scoring.py` (score
expliqué), `radar/pipeline.py` (dédoublonnage, expiration, alertes), `radar/store.py` (base),
`web/` (tableau de bord), `supabase/schema.sql`, `.github/workflows/` (robot, site, sauvegardes).
