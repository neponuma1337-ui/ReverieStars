import asyncio, hashlib, hmac, json, logging, os, re, secrets, sqlite3, time
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import parse_qsl
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, ReplyKeyboardMarkup, KeyboardButton, LabeledPrice, PreCheckoutQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

load_dotenv()
TOKEN=os.getenv('BOT_TOKEN','').strip(); ADMIN_IDS={int(x) for x in os.getenv('ADMIN_IDS','').split(',') if x.strip().isdigit()}
WEBAPP_URL=os.getenv('WEBAPP_URL','').strip(); API_HOST=os.getenv('API_HOST','0.0.0.0'); API_PORT=int(os.getenv('PORT',os.getenv('API_PORT','8080')))
API_BASE=os.getenv('API_BASE_URL','').rstrip('/'); SUPPORT=os.getenv('SUPPORT_USERNAME','neponuma').lstrip('@'); DB=os.getenv('DB_PATH', 'shop.db' if os.path.exists('shop.db') and not os.path.exists('reveriestars.db') else 'reveriestars.db')
if not TOKEN: raise RuntimeError('BOT_TOKEN is missing. Copy .env.example to .env and add your BotFather token.')
logging.basicConfig(level=logging.INFO); log=logging.getLogger('reveriestars')
bot=Bot(TOKEN); dp=Dispatcher(); CURRENCY='XTR'
PRODUCTS={
 'stars_100':('100 Telegram Stars','⭐','stars',int(os.getenv('PRICE_STARS_100','120'))),
 'stars_250':('250 Telegram Stars','🌟','stars',int(os.getenv('PRICE_STARS_250','290'))),
 'stars_500':('500 Telegram Stars','💫','stars',int(os.getenv('PRICE_STARS_500','570'))),
 'stars_1000':('1 000 Telegram Stars','✨','stars',int(os.getenv('PRICE_STARS_1000','1100'))),
 'premium_3':('Telegram Premium · 3 месяца','♛','premium',int(os.getenv('PRICE_PREMIUM_3','1500'))),
 'premium_6':('Telegram Premium · 6 месяцев','👑','premium',int(os.getenv('PRICE_PREMIUM_6','2700'))),
 'premium_12':('Telegram Premium · 12 месяцев','💎','premium',int(os.getenv('PRICE_PREMIUM_12','4900'))),
}
CB={k:int(os.getenv('CASHBACK_'+k.upper(),v)) for k,v in [('bronze',2),('silver',3),('gold',4),('diamond',5)]}
REF_BONUS=int(os.getenv('REFERRAL_BONUS','30'))
STATUSES={'awaiting_payment':'Ожидает оплаты','paid_pending_fulfillment':'Оплачен · готовится','processing':'Выполняется','completed':'Завершён','cancelled':'Отменён','payment_error':'Ошибка оплаты'}

@contextmanager
def conn():
 c=sqlite3.connect(DB,timeout=20); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON')
 try:
  yield c; c.commit()
 except Exception:
  c.rollback(); raise
 finally: c.close()
def now(): return datetime.now(timezone.utc).isoformat()
def init_db():
 with conn() as c:
  c.executescript('''
  CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, joined_at TEXT, referrer_id INTEGER, balance INTEGER NOT NULL DEFAULT 0, total_spent INTEGER NOT NULL DEFAULT 0, vip TEXT NOT NULL DEFAULT 'bronze', referral_paid INTEGER NOT NULL DEFAULT 0);
  CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,username TEXT,product_key TEXT NOT NULL,recipient TEXT NOT NULL,amount INTEGER NOT NULL,discount INTEGER NOT NULL DEFAULT 0,wallet_used INTEGER NOT NULL DEFAULT 0,pay_amount INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'awaiting_payment',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,telegram_charge_id TEXT,reviewed INTEGER NOT NULL DEFAULT 0,FOREIGN KEY(user_id) REFERENCES users(user_id));
  CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,delta INTEGER NOT NULL,kind TEXT NOT NULL,description TEXT NOT NULL,order_id INTEGER,created_at TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS promos(code TEXT PRIMARY KEY,percent INTEGER NOT NULL,max_uses INTEGER NOT NULL DEFAULT 100,uses INTEGER NOT NULL DEFAULT 0,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS promo_uses(code TEXT NOT NULL,user_id INTEGER NOT NULL,order_id INTEGER NOT NULL,UNIQUE(code,user_id));
  CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,username TEXT,rating INTEGER NOT NULL,comment TEXT NOT NULL,created_at TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
  ''')
  # Safe in-place migrations for earlier ReverieStars databases.
  cols={r['name'] for r in c.execute('PRAGMA table_info(orders)').fetchall()}
  additions={'username':"TEXT DEFAULT ''",'discount':'INTEGER NOT NULL DEFAULT 0','wallet_used':'INTEGER NOT NULL DEFAULT 0','pay_amount':'INTEGER NOT NULL DEFAULT 0','updated_at':"TEXT NOT NULL DEFAULT ''",'reviewed':'INTEGER NOT NULL DEFAULT 0'}
  for name,decl in additions.items():
   if name not in cols: c.execute(f'ALTER TABLE orders ADD COLUMN {name} {decl}')
  c.execute("UPDATE orders SET pay_amount=amount WHERE pay_amount=0 AND amount>0")
  c.execute("UPDATE orders SET updated_at=created_at WHERE updated_at=''")
  c.execute("INSERT OR IGNORE INTO settings VALUES('announcement','')")

def ensure_user(u, ref=None):
 with conn() as c:
  row=c.execute('SELECT user_id FROM users WHERE user_id=?',(u.id,)).fetchone()
  if not row:
   valid_ref=ref if ref and ref!=u.id and c.execute('SELECT 1 FROM users WHERE user_id=?',(ref,)).fetchone() else None
   c.execute('INSERT INTO users(user_id,username,first_name,joined_at,referrer_id) VALUES(?,?,?,?,?)',(u.id,u.username or '',u.first_name or '',now(),valid_ref))
  else: c.execute('UPDATE users SET username=?,first_name=? WHERE user_id=?',(u.username or '',u.first_name or '',u.id))

def vip_for(spent): return 'diamond' if spent>=20000 else 'gold' if spent>=8000 else 'silver' if spent>=2500 else 'bronze'
def cashback(vip): return CB.get(vip,2)
def add_ledger(c,uid,delta,kind,desc,oid=None):
 c.execute('UPDATE users SET balance=MAX(0,balance+?) WHERE user_id=?',(delta,uid)); c.execute('INSERT INTO ledger(user_id,delta,kind,description,order_id,created_at) VALUES(?,?,?,?,?,?)',(uid,delta,kind,desc,oid,now()))
def order_text(r): return f"№{r['id']} · {PRODUCTS.get(r['product_key'],('Товар',))[0]}\nПолучатель: @{r['recipient']}\nСтатус: {STATUSES.get(r['status'],r['status'])}"
def admin_kb(oid): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⚙️ В работу',callback_data=f'work:{oid}'),InlineKeyboardButton(text='✅ Выполнен',callback_data=f'done:{oid}')],[InlineKeyboardButton(text='❌ Отменить',callback_data=f'cancel:{oid}')]])
def main_menu():
 rows=[]
 if WEBAPP_URL.startswith('https://'): rows.append([KeyboardButton(text='✦ Открыть ReverieStars',web_app=WebAppInfo(url=WEBAPP_URL))])
 rows += [[KeyboardButton(text='📦 Мои заказы'),KeyboardButton(text='💎 Reverie Club')],[KeyboardButton(text='🆘 Поддержка')]]
 return ReplyKeyboardMarkup(keyboard=rows,resize_keyboard=True)
async def notify_admins(text, reply=None):
 for aid in ADMIN_IDS:
  try: await bot.send_message(aid,text,reply_markup=reply)
  except Exception as e: log.warning('Admin notify failed: %s',e)
async def set_status(oid,status):
 with conn() as c:
  r=c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
  if not r: return None
  c.execute('UPDATE orders SET status=?,updated_at=? WHERE id=?',(status,now(),oid))
  return dict(r)
async def complete_order(oid):
 with conn() as c:
  r=c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
  if not r: return False,'Заказ не найден.'
  if r['status']=='completed': return False,'Заказ уже завершён.'
  if r['status'] not in ('paid_pending_fulfillment','processing'): return False,'Нельзя завершить неоплаченный заказ.'
  c.execute("UPDATE orders SET status='completed',updated_at=? WHERE id=?",(now(),oid))
  u=c.execute('SELECT * FROM users WHERE user_id=?',(r['user_id'],)).fetchone()
  spent=u['total_spent']+r['amount']-r['discount']; newvip=vip_for(spent); c.execute('UPDATE users SET total_spent=?,vip=? WHERE user_id=?',(spent,newvip,r['user_id']))
  bonus=max(1,round((r['amount']-r['discount'])*cashback(newvip)/100)); add_ledger(c,r['user_id'],bonus,'cashback',f'Кэшбэк за заказ №{oid}',oid)
  if u['referrer_id'] and not u['referral_paid']:
   add_ledger(c,u['referrer_id'],REF_BONUS,'referral',f'Бонус за приглашённого пользователя',oid); add_ledger(c,r['user_id'],REF_BONUS,'referral_welcome','Приветственный бонус за первую покупку',oid); c.execute('UPDATE users SET referral_paid=1 WHERE user_id=?',(r['user_id'],)); refid=u['referrer_id']
  else: refid=None
 try:
  await bot.send_message(r['user_id'],f'🎉 <b>Заказ №{oid} выполнен!</b>\nСпасибо, что выбираете ReverieStars. Кэшбэк начислен на баланс.',parse_mode='HTML')
  if refid: await bot.send_message(refid,f'💎 Ваш приглашённый пользователь совершил первую покупку. Начислено {REF_BONUS}⭐ бонусов!')
 except Exception: pass
 return True,'Заказ завершён, кэшбэк начислен.'

# Telegram Mini App initData verification
def auth_user(request):
 raw=request.headers.get('X-Telegram-Init-Data','')
 if not raw: raise web.HTTPUnauthorized(text='Missing Telegram initData')
 try:
  pairs=dict(parse_qsl(raw,keep_blank_values=True)); their=pairs.pop('hash'); auth_date=int(pairs.get('auth_date','0'))
  if abs(time.time()-auth_date)>86400: raise ValueError('expired')
  data='\n'.join(f'{k}={v}' for k,v in sorted(pairs.items()))
  secret=hmac.new(b'WebAppData',TOKEN.encode(),hashlib.sha256).digest()
  expected=hmac.new(secret,data.encode(),hashlib.sha256).hexdigest()
  if not hmac.compare_digest(expected,their): raise ValueError('bad hash')
  user=json.loads(pairs['user']); return user
 except Exception: raise web.HTTPUnauthorized(text='Invalid or expired Telegram initData')
def get_authed(request):
 u=auth_user(request)
 class U: pass
 x=U(); x.id=int(u['id']); x.username=u.get('username'); x.first_name=u.get('first_name',''); return x
async def health(req):
 return web.json_response({'ok':True,'service':'reveriestars-api'})
async def api_config(req):
 return web.Response(text="window.REVERIE_BOT_USERNAME='ReverieStars_bot';",content_type='application/javascript',headers={'Cache-Control':'no-store'})
async def api_catalog(req):
 return web.json_response({'products':[{'key':k,'title':v[0],'icon':v[1],'group':v[2],'price':v[3]} for k,v in PRODUCTS.items()]})
async def api_me(req):
 u=get_authed(req); ensure_user(u)
 with conn() as c:
  x=c.execute('SELECT * FROM users WHERE user_id=?',(u.id,)).fetchone()
  referrals=c.execute('SELECT COUNT(*) n FROM users WHERE referrer_id=?',(u.id,)).fetchone()['n']
  orders=c.execute('SELECT COUNT(*) n FROM orders WHERE user_id=?',(u.id,)).fetchone()['n']
  ledger=c.execute('SELECT delta,kind,description,created_at FROM ledger WHERE user_id=? ORDER BY id DESC LIMIT 8',(u.id,)).fetchall()
  spent=c.execute('SELECT COALESCE(SUM(amount-discount),0) n FROM orders WHERE user_id=? AND status="completed"',(u.id,)).fetchone()['n']
 return web.json_response({'user':{'id':u.id,'username':x['username'],'first_name':x['first_name'],'balance':x['balance'],'vip':x['vip'],'cashback':cashback(x['vip']),'total_spent':spent,'orders':orders,'referrals':referrals,'referral_code':str(u.id)},'ledger':[dict(z) for z in ledger],'support':SUPPORT})
async def api_orders(req):
 u=get_authed(req); ensure_user(u)
 try: d=await req.json(); key=str(d.get('product','')); recipient=str(d.get('recipient','')).strip().lstrip('@'); promo=str(d.get('promo','')).strip().upper(); use_wallet=bool(d.get('use_wallet',False))
 except Exception: raise web.HTTPBadRequest(text='Invalid JSON')
 if key not in PRODUCTS or not re.fullmatch(r'[A-Za-z0-9_]{5,32}',recipient): raise web.HTTPBadRequest(text='Invalid product or username')
 title,_,_,price=PRODUCTS[key]; discount=0
 with conn() as c:
  if promo:
   pr=c.execute('SELECT * FROM promos WHERE code=? AND active=1',(promo,)).fetchone()
   used=c.execute('SELECT 1 FROM promo_uses WHERE code=? AND user_id=?',(promo,u.id)).fetchone()
   if not pr or pr['uses']>=pr['max_uses'] or used: raise web.HTTPBadRequest(text='Promo code is invalid, expired, or already used')
   discount=min(price,price*pr['percent']//100)
  wallet=0
  if use_wallet:
   user=c.execute('SELECT balance FROM users WHERE user_id=?',(u.id,)).fetchone()
   # Wallet funds are deducted at order creation, so pending orders are already reserved.
   wallet=min(max(0,user['balance']),max(0,price-discount))
  pay=max(0,price-discount-wallet)
  cur=c.execute('INSERT INTO orders(user_id,username,product_key,recipient,amount,discount,wallet_used,pay_amount,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(u.id,u.username or '',key,recipient,price,discount,wallet,pay,'awaiting_payment',now(),now())); oid=cur.lastrowid
  if promo: c.execute('INSERT INTO promo_uses(code,user_id,order_id) VALUES(?,?,?)',(promo,u.id,oid)); c.execute('UPDATE promos SET uses=uses+1 WHERE code=?',(promo,))
  if wallet: add_ledger(c,u.id,-wallet,'wallet_spend',f'Резерв баланса для заказа №{oid}',oid)
 if pay==0:
  with conn() as c: c.execute("UPDATE orders SET status='paid_pending_fulfillment' WHERE id=?",(oid,))
  await notify_admins(f'🆕 <b>Заказ №{oid} оплачен балансом</b>\n{title}\nПолучатель: @{recipient}\nПокупатель: {u.id}',admin_kb(oid))
  return web.json_response({'ok':True,'order_id':oid,'paid_by_wallet':True,'message':'Заказ оплачен бонусным балансом и отправлен администратору.'})
 try:
  await bot.send_invoice(chat_id=u.id,title=title,description=f'ReverieStars · заказ №{oid} · получатель @{recipient}',payload=f'order:{oid}',currency=CURRENCY,prices=[LabeledPrice(label=title,amount=pay)],provider_token='')
 except Exception as e:
  with conn() as c:
   if wallet: add_ledger(c,u.id,wallet,'wallet_refund',f'Возврат резерва заказа №{oid}',oid)
   c.execute("UPDATE orders SET status='payment_error' WHERE id=?",(oid,))
  log.exception('Invoice error'); raise web.HTTPBadRequest(text='Could not create Telegram invoice')
 await notify_admins(f'🛍 Новый заказ №{oid}\n{title}\n@{recipient}\nПользователь: {u.id}\nК оплате: {pay}⭐')
 return web.json_response({'ok':True,'order_id':oid,'pay_amount':pay,'discount':discount,'wallet_used':wallet,'message':'Счёт отправлен в чат с ботом. Вернись в Telegram и оплати его.'})
async def api_myorders(req):
 u=get_authed(req)
 with conn() as c: rows=c.execute('SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 30',(u.id,)).fetchall()
 return web.json_response({'orders':[{'id':r['id'],'product':PRODUCTS.get(r['product_key'],('Товар',))[0],'recipient':r['recipient'],'amount':r['amount'],'discount':r['discount'],'status':r['status'],'status_label':STATUSES.get(r['status'],r['status']),'created_at':r['created_at']} for r in rows]})
async def api_reviews(req):
 if req.method=='GET':
  with conn() as c: rows=c.execute('SELECT username,rating,comment,created_at FROM reviews ORDER BY id DESC LIMIT 20').fetchall()
  return web.json_response({'reviews':[dict(r) for r in rows]})
 u=get_authed(req)
 try: d=await req.json(); rating=int(d.get('rating',5)); comment=str(d.get('comment','')).strip()[:500]
 except Exception: raise web.HTTPBadRequest()
 if rating<1 or rating>5 or len(comment)<3: raise web.HTTPBadRequest(text='Review must be 3-500 characters and rating 1-5')
 with conn() as c:
  done=c.execute("SELECT id FROM orders WHERE user_id=? AND status='completed' ORDER BY id DESC LIMIT 1",(u.id,)).fetchone()
  if not done: raise web.HTTPForbidden(text='Review available after a completed order')
  c.execute('INSERT INTO reviews(user_id,username,rating,comment,created_at) VALUES(?,?,?,?,?)',(u.id,getattr(u,'username',None) or '',rating,comment,now()))
 return web.json_response({'ok':True})

@dp.message(CommandStart())
async def start(m:Message, command):
 ref=None
 arg=(command.args or '').strip()
 if arg.startswith('ref_') and arg[4:].isdigit(): ref=int(arg[4:])
 ensure_user(m.from_user,ref)
 text='✨ <b>ReverieStars</b>\nПремиальный магазин Telegram Stars и Premium.\n\nДобро пожаловать в пространство цифровых привилегий.'
 if not WEBAPP_URL.startswith('https://'): text+='\n\n⚙️ Mini App ещё не опубликован: настрой HTTPS в WEBAPP_URL.'
 await m.answer(text,reply_markup=main_menu(),parse_mode='HTML')
@dp.message(F.text=='📦 Мои заказы')
async def my_orders(m:Message):
 with conn() as c: rows=c.execute('SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10',(m.from_user.id,)).fetchall()
 await m.answer('\n\n'.join(order_text(r) for r in rows) if rows else 'Пока нет заказов.',parse_mode='HTML')
@dp.message(F.text=='💎 Reverie Club')
async def club(m:Message):
 with conn() as c: u=c.execute('SELECT * FROM users WHERE user_id=?',(m.from_user.id,)).fetchone(); n=c.execute('SELECT COUNT(*) n FROM users WHERE referrer_id=?',(m.from_user.id,)).fetchone()['n']
 if not u: ensure_user(m.from_user); return await club(m)
 link=f'https://t.me/{(await bot.get_me()).username}?start=ref_{m.from_user.id}'
 await m.answer(f"<b>💎 REVERIE CLUB</b>\nУровень: <b>{u['vip'].upper()}</b>\nБаланс: <b>{u['balance']}⭐</b>\nКэшбэк: <b>{cashback(u['vip'])}%</b>\nПотрачено: {u['total_spent']}⭐\nПриглашено: {n}\n\nТвоя ссылка:\n<code>{link}</code>",parse_mode='HTML')
@dp.message(F.text=='🆘 Поддержка')
async def support(m:Message): await m.answer(f'Поддержка ReverieStars: @{SUPPORT}')
@dp.message(Command('admin'))
async def admin(m:Message):
 if m.from_user.id not in ADMIN_IDS: return
 with conn() as c:
  total=c.execute('SELECT COUNT(*) n FROM orders').fetchone()['n']; paid=c.execute("SELECT COUNT(*) n FROM orders WHERE status IN ('paid_pending_fulfillment','processing','completed')").fetchone()['n']; revenue=c.execute("SELECT COALESCE(SUM(pay_amount),0) n FROM orders WHERE status IN ('paid_pending_fulfillment','processing','completed')").fetchone()['n']; users=c.execute('SELECT COUNT(*) n FROM users').fetchone()['n']; pending=c.execute("SELECT COUNT(*) n FROM orders WHERE status='paid_pending_fulfillment'").fetchone()['n']; spend=c.execute("SELECT COALESCE(SUM(amount-discount),0) n FROM orders WHERE status='completed'").fetchone()['n']; rows=c.execute('SELECT * FROM orders ORDER BY id DESC LIMIT 12').fetchall()
 txt=f'<b>REVERIESTARS · ADMIN</b>\nПользователи: {users}\nЗаказы: {total} · оплачено/в работе/завершено: {paid}\nОжидают выполнения: {pending}\nОплачено через Telegram: {revenue}⭐\nЗавершённые продажи: {spend}⭐\n\n<b>Последние заказы:</b>'
 for r in rows: txt+=f"\n№{r['id']} · {PRODUCTS[r['product_key']][0]} · {r['pay_amount']}⭐ · {STATUSES.get(r['status'])}"
 await m.answer(txt,parse_mode='HTML')
@dp.message(Command('done'))
async def done_cmd(m:Message):
 if m.from_user.id not in ADMIN_IDS:return
 p=(m.text or '').split();
 if len(p)!=2 or not p[1].isdigit(): return await m.answer('Использование: /done НОМЕР')
 ok,msg=await complete_order(int(p[1])); await m.answer(msg)
@dp.message(Command('work'))
async def work_cmd(m:Message):
 if m.from_user.id not in ADMIN_IDS:return
 p=(m.text or '').split()
 if len(p)!=2 or not p[1].isdigit(): return await m.answer('Использование: /work НОМЕР')
 r=await set_status(int(p[1]),'processing'); await m.answer('Заказ переведён в выполнение.' if r else 'Заказ не найден.')
@dp.message(Command('cancel'))
async def cancel_cmd(m:Message):
 if m.from_user.id not in ADMIN_IDS:return
 p=(m.text or '').split()
 if len(p)!=2 or not p[1].isdigit(): return await m.answer('Использование: /cancel НОМЕР')
 oid=int(p[1])
 with conn() as c:
  r=c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
  if not r:return await m.answer('Заказ не найден.')
  if r['status']!='awaiting_payment': return await m.answer('Можно отменить только неоплаченный заказ.')
  c.execute("UPDATE orders SET status='cancelled' WHERE id=?",(oid,))
  if r['wallet_used']: add_ledger(c,r['user_id'],r['wallet_used'],'wallet_refund',f'Возврат баланса за отменённый заказ №{oid}',oid)
  used=c.execute('SELECT code FROM promo_uses WHERE order_id=?',(oid,)).fetchone()
  if used: c.execute('DELETE FROM promo_uses WHERE order_id=?',(oid,)); c.execute('UPDATE promos SET uses=MAX(0,uses-1) WHERE code=?',(used['code'],))
 await m.answer('Заказ отменён, резерв баланса возвращён.')
@dp.message(Command('promo_add'))
async def promo_add(m:Message):
 if m.from_user.id not in ADMIN_IDS:return
 p=(m.text or '').split()
 if len(p)<3 or not p[2].isdigit(): return await m.answer('Использование: /promo_add CODE ПРОЦЕНТ [ЛИМИТ]')
 code=p[1].upper(); percent=int(p[2]); limit=int(p[3]) if len(p)>3 and p[3].isdigit() else 100
 if not re.fullmatch('[A-Z0-9_-]{3,24}',code) or not 1<=percent<=50 or limit<1:return await m.answer('Код: 3-24 символа, скидка 1-50%, лимит от 1.')
 with conn() as c: c.execute('INSERT OR REPLACE INTO promos(code,percent,max_uses,uses,active,created_at) VALUES(?,?,?,0,1,?)',(code,percent,limit,now()))
 await m.answer(f'Промокод {code}: скидка {percent}%, лимит {limit}.')
@dp.message(Command('broadcast'))
async def broadcast(m:Message):
 if m.from_user.id not in ADMIN_IDS:return
 msg=(m.text or '').partition(' ')[2].strip()
 if not msg:return await m.answer('Использование: /broadcast текст')
 with conn() as c: ids=[r['user_id'] for r in c.execute('SELECT user_id FROM users').fetchall()]
 ok=0
 for uid in ids:
  try: await bot.send_message(uid,'✦ <b>REVERIESTARS</b>\n\n'+msg,parse_mode='HTML'); ok+=1
  except Exception: pass
 await m.answer(f'Рассылка завершена: {ok}/{len(ids)}')
@dp.callback_query(F.data.startswith(('work:','done:','cancel:')))
async def order_action(q):
 if q.from_user.id not in ADMIN_IDS:return await q.answer('Нет доступа',show_alert=True)
 action,raw=q.data.split(':'); oid=int(raw)
 if action=='done': ok,msg=await complete_order(oid)
 elif action=='work': r=await set_status(oid,'processing'); ok=bool(r); msg='Заказ в работе.' if r else 'Заказ не найден.'
 else:
  with conn() as c:
   r=c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
   if r and r['status']=='awaiting_payment':
    c.execute("UPDATE orders SET status='cancelled' WHERE id=?",(oid,));
    if r['wallet_used']: add_ledger(c,r['user_id'],r['wallet_used'],'wallet_refund',f'Возврат за отмену №{oid}',oid)
    used=c.execute('SELECT code FROM promo_uses WHERE order_id=?',(oid,)).fetchone()
    if used: c.execute('DELETE FROM promo_uses WHERE order_id=?',(oid,)); c.execute('UPDATE promos SET uses=MAX(0,uses-1) WHERE code=?',(used['code'],))
    ok=True; msg='Заказ отменён, баланс возвращён.'
   else: ok=False; msg='Нельзя отменить этот заказ.'
 await q.answer(msg,show_alert=True)
 try: await q.message.edit_reply_markup(reply_markup=None)
 except Exception: pass
@dp.pre_checkout_query()
async def pre_checkout(q:PreCheckoutQuery):
 try:
  oid=int(q.invoice_payload.split(':')[1])
  with conn() as c: r=c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
  ok=bool(r and r['status']=='awaiting_payment' and r['pay_amount']==q.total_amount)
  await q.answer(ok=ok,error_message=None if ok else 'Заказ уже недоступен. Напишите в поддержку.')
 except Exception: await q.answer(ok=False,error_message='Invalid order')
@dp.message(F.successful_payment)
async def paid(m:Message):
 payload=m.successful_payment.invoice_payload
 if not payload.startswith('order:'):return
 oid=int(payload.split(':',1)[1]); charge=m.successful_payment.telegram_payment_charge_id
 with conn() as c:
  r=c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
  if not r or r['user_id']!=m.from_user.id:return
  c.execute("UPDATE orders SET status='paid_pending_fulfillment',telegram_charge_id=?,updated_at=? WHERE id=?",(charge,now(),oid))
 await m.answer(f'✅ Оплата получена! Заказ №{oid} передан в обработку. Статус можно проверить в магазине.')
 await notify_admins(f'💰 <b>ОПЛАЧЕНО · заказ №{oid}</b>\n{PRODUCTS[r["product_key"]][0]}\nПолучатель: @{r["recipient"]}\nПокупатель: {r["user_id"]}',admin_kb(oid))

async def on_startup(app):
 init_db(); me=await bot.get_me(); log.info('Bot @%s started; API port %s',me.username,API_PORT)
async def on_cleanup(app): await bot.session.close()
@web.middleware
async def cors_middleware(request, handler):
 if request.method=='OPTIONS': response=web.Response(status=204)
 else: response=await handler(request)
 response.headers['Access-Control-Allow-Origin']='*'; response.headers['Access-Control-Allow-Headers']='Content-Type, X-Telegram-Init-Data'; response.headers['Access-Control-Allow-Methods']='GET, POST, OPTIONS'; response.headers['Access-Control-Max-Age']='86400'
 return response
def create_api():
 app=web.Application(client_max_size=64*1024,middlewares=[cors_middleware]); app.router.add_get('/health',health); app.router.add_get('/config.js',api_config); app.router.add_get('/api/catalog',api_catalog); app.router.add_get('/api/me',api_me); app.router.add_post('/api/orders',api_orders); app.router.add_get('/api/orders',api_myorders); app.router.add_get('/api/reviews',api_reviews); app.router.add_post('/api/reviews',api_reviews); app.on_startup.append(on_startup); app.on_cleanup.append(on_cleanup); return app
async def main():
 init_db(); runner=web.AppRunner(create_api()); await runner.setup(); site=web.TCPSite(runner,API_HOST,API_PORT); await site.start(); log.info('API listening on %s:%s',API_HOST,API_PORT)
 try: await dp.start_polling(bot)
 finally: await runner.cleanup()
if __name__=='__main__': asyncio.run(main())
