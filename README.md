# ReverieStars — Bot + Mini App + API

## What is included
- aiohttp API and aiogram Telegram bot in `app.py`
- Mini App frontend in `web/`
- SQLite schema with safe additive migrations
- Orders, Telegram Stars invoices (XTR), cashback, VIP levels, referral bonuses, promo codes, reviews, admin commands
- Render Blueprint with persistent disk and `/health` health check

## Important deployment notes
1. This Render Blueprint uses the **Starter** web-service plan and a **1 GB persistent disk**. It is not a free deployment. Do not deploy until you have reviewed the current price shown by Render.
2. The persistent disk is required for the included SQLite database. Do not remove it or move `DB_PATH` outside `/var/data` on Render; otherwise data can be lost on redeploy/restart.
3. `WEBAPP_URL` must be the final public HTTPS URL of your Netlify Mini App. Set it after Netlify publishes the `web/` folder.
4. Set `BOT_TOKEN` and `ADMIN_IDS` as Render environment variables. `ADMIN_IDS` is a comma-separated list of numeric Telegram user IDs. Never put the real bot token in GitHub or in the frontend.
5. In `web/config.js`, replace `https://YOUR-API-HOST` with the deployed Render service URL, then redeploy the `web/` folder to Netlify.
6. The bot uses Telegram Stars invoices (`XTR`). Verify product pricing and fulfillment rules before accepting real payments. Telegram Stars payments are not a manual card-payment gateway.

## Render deployment
- Connect this GitHub repository to Render.
- Choose the Blueprint / `render.yaml` deployment flow (or create a Python Web Service with the same settings).
- Review the paid Starter plan and persistent disk charge before confirming.
- Add `BOT_TOKEN`, `ADMIN_IDS`, and the final `WEBAPP_URL`.
- After deploy, check `https://YOUR-SERVICE.onrender.com/health` — it should return `{"ok":true,"service":"reveriestars-api"}`.

## Netlify Drop
Upload the **contents of the `web/` directory**, not the whole repository. The uploaded directory must have `index.html` at its root. Once Netlify gives you the site URL, set it as `WEBAPP_URL` in Render.

## Local run
```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env with your own values
python app.py
```

## Environment variables
See `.env.example`. Prices are configured in Telegram Stars units (`XTR`).
