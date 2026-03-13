import logging
import json
import os
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, PreCheckoutQueryHandler, filters, ContextTypes
)

# ─── НАСТРОЙКИ ───────────────────────────────────────────────────────────────
TOKEN = "8378117030:AAH5qB5YwzESdSJ-jxtzyQdXoukzHqt9x9U"
ADMIN_CODE = "16.05.2013"
CARD_NUMBER = "2200701233455606"
DELIVERY_PRICE = 5
REF_BONUS = 5         # ₽ за каждого приглашённого
REF_REQUIRED = 1      # бонус за каждого (не накопительно)
DATA_FILE = "bot_data.json"

# Курс звёзд: 1 звезда = 1 рубль (настрой под себя)
STARS_RATE = 1  # 1 XTR = 1 ₽

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

AGREEMENT_TEXT = """📋 <b>Пользовательское соглашение</b>

Добро пожаловать в школьный магазин-бот!
Перед началом использования ознакомьтесь с условиями:

1️⃣ <b>Ответственность.</b> Администрация бота не несёт ответственности за задержки, ошибки при переводе или иные ситуации, возникшие по вине третьих лиц (банков, платёжных систем).

2️⃣ <b>Оплата.</b> Все платежи осуществляются добровольно. После подтверждения перевода возврат средств не предусмотрен, кроме случаев когда товар не был предоставлен.

3️⃣ <b>Доставка.</b> Доставка осуществляется в рамках школы / договорённого места. Администрация не несёт ответственности за утерю товара после передачи покупателю.

4️⃣ <b>Товары.</b> Все товары — канцелярские принадлежности для учёбы. Администрация не гарантирует наличие конкретного товара и вправе отказать в продаже.

5️⃣ <b>Персональные данные.</b> Бот использует только ваш Telegram-юзернейм для идентификации заказа и ничего более.

6️⃣ <b>Споры.</b> Все спорные ситуации решаются через @omunv.

Нажимая «✅ Принять», вы подтверждаете что ознакомились и согласны с данными условиями."""

# ─── ХРАНИЛИЩЕ ───────────────────────────────────────────────────────────────
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"products": [], "orders": [], "users": {}, "admin_id": None}

def save_data(d):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

data = load_data()

# ─── ВСПОМОГАТЕЛЬНЫЕ ─────────────────────────────────────────────────────────
def get_user(user_id: int):
    uid = str(user_id)
    if uid not in data["users"]:
        data["users"][uid] = {
            "balance": 0, "referrals": 0,
            "referred_by": None, "username": "", "agreed": False
        }
    return data["users"][uid]

def is_admin(user_id: int):
    return data.get("admin_id") == user_id

def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Добавить товар", callback_data="admin_add")],
        [InlineKeyboardButton("📋 Заявки на оплату", callback_data="admin_orders")],
        [InlineKeyboardButton("💳 Заявки на пополнение", callback_data="admin_topup_orders")],
        [InlineKeyboardButton("🗂 Мои товары", callback_data="admin_products")],
    ])

def product_card_text(p):
    digital_badge = " <b>💻 Цифровой</b>" if p.get("digital") else ""
    text = f"🏷 <b>{p['name']}</b>{digital_badge}\n"
    text += f"💰 Цена: <b>{p['price']}₽</b>\n"
    if p.get("digital"):
        text += f"📲 Отправляется сразу после оплаты"
    else:
        text += f"📦 В наличии: {p['count']} шт."
    if p.get("description"):
        text += f"\n\n📝 {p['description']}"
    return text

# ─── /start ──────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = get_user(user.id)
    u["username"] = user.username or user.first_name

    args = context.args
    if args and args[0].startswith("ref"):
        ref_id = args[0][3:]
        if ref_id != str(user.id) and not u.get("referred_by"):
            u["referred_by"] = ref_id
            ref_user = data["users"].get(ref_id)
            if ref_user:
                ref_user["referrals"] = ref_user.get("referrals", 0) + 1
                ref_user["balance"] = ref_user.get("balance", 0) + REF_BONUS
                invitee_name = f"@{user.username}" if user.username else user.first_name
                try:
                    await context.bot.send_message(
                        int(ref_id),
                        f"🎉 По вашей ссылке зашёл {invitee_name}!\n"
                        f"💰 +{REF_BONUS}₽ начислено на баланс.\n"
                        f"Всего приглашено: {ref_user['referrals']} чел."
                    )
                except:
                    pass
    save_data(data)

    if u.get("agreed"):
        await show_shop_msg(update.message, context)
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Принять и продолжить", callback_data="agree")],
        [InlineKeyboardButton("❌ Отказаться", callback_data="disagree")],
    ])
    await update.message.reply_text(AGREEMENT_TEXT, reply_markup=keyboard, parse_mode="HTML")

# ─── МАГАЗИН ─────────────────────────────────────────────────────────────────
def shop_keyboard():
    available = [p for p in data["products"] if p["count"] > 0]
    if not available:
        return "😔 Товаров пока нет. Загляни позже!", InlineKeyboardMarkup([
            [InlineKeyboardButton("👤 Мой профиль", callback_data="profile")],
            [InlineKeyboardButton("💳 Пополнить баланс", callback_data="topup_menu")],
            [InlineKeyboardButton("🔗 Реферальная ссылка", callback_data="referral")],
        ])
    text = "🛍 <b>Каталог товаров</b>\n\nВыбери что хочешь купить:"
    buttons = []
    for i, p in enumerate(data["products"]):
        if p["count"] > 0:
            buttons.append([InlineKeyboardButton(
                f"🛒 {p['name']} — {p['price']}₽",
                callback_data=f"view_{i}"
            )])
    buttons.append([InlineKeyboardButton("👤 Мой профиль", callback_data="profile")])
    buttons.append([InlineKeyboardButton("💳 Пополнить баланс", callback_data="topup_menu")])
    buttons.append([InlineKeyboardButton("🔗 Реферальная ссылка", callback_data="referral")])
    return text, InlineKeyboardMarkup(buttons)

async def show_shop_msg(message, context):
    text, kb = shop_keyboard()
    await message.reply_text(text, reply_markup=kb, parse_mode="HTML")

async def show_shop_query(query, context):
    text, kb = shop_keyboard()
    try:
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except:
        await query.message.reply_text(text, reply_markup=kb, parse_mode="HTML")

# ─── ОПЛАТА ЗАКАЗА ───────────────────────────────────────────────────────────
async def ask_payment(target, context, order_id):
    order = next((o for o in data["orders"] if o["id"] == order_id), None)
    if not order:
        return

    u = get_user(order["user_id"])
    balance = u.get("balance", 0)
    price = order["price"]

    text = (
        f"💳 <b>Оплата заказа #{order_id}</b>\n\n"
        f"📦 Товар: {order['product']}\n"
        f"💰 Сумма: <b>{price}₽</b>\n"
        f"{'🚚 Доставка включена' if order['delivery'] else '🚶 Самовывоз'}\n"
        f"💼 Ваш баланс: <b>{balance}₽</b>\n\n"
    )

    buttons = []

    # Оплата с баланса
    if balance >= price:
        text += f"У вас достаточно средств на балансе для оплаты!"
        buttons.append([InlineKeyboardButton(f"💼 Оплатить с баланса ({price}₽)", callback_data=f"pay_balance_{order_id}")])
    else:
        text += (
            f"Переведите <b>{price}₽</b> на карту Т-Банк:\n"
            f"<code>{CARD_NUMBER}</code>\n\n"
            f"В комментарии укажите: <b>@{order['username']}</b>\n"
            f"Если не можете — напишите @omunv и скиньте чек (PDF или скрин)."
        )
        buttons.append([InlineKeyboardButton("✅ Я отправил деньги", callback_data=f"paid_{order_id}")])
        if balance > 0:
            buttons.append([InlineKeyboardButton(f"💼 Частичная оплата с баланса ({balance}₽)", callback_data=f"pay_balance_{order_id}")])

    # Оплата звёздами
    stars_needed = price * STARS_RATE
    buttons.append([InlineKeyboardButton(f"⭐️ Оплатить {stars_needed} звёзд", callback_data=f"pay_stars_{order_id}")])
    buttons.append([InlineKeyboardButton("❌ Отменить", callback_data="shop")])

    keyboard = InlineKeyboardMarkup(buttons)
    if hasattr(target, "edit_message_text"):
        try:
            await target.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")
        except:
            await target.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await target.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")

# ─── ПОПОЛНЕНИЕ БАЛАНСА ──────────────────────────────────────────────────────
async def show_topup_menu(query, context):
    user = query.from_user
    u = get_user(user.id)
    text = (
        f"💳 <b>Пополнение баланса</b>\n\n"
        f"Текущий баланс: <b>{u.get('balance', 0)}₽</b>\n\n"
        f"Выберите способ пополнения:"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏦 Перевод на карту", callback_data="topup_card")],
        [InlineKeyboardButton("⭐️ Звёздами Telegram", callback_data="topup_stars")],
        [InlineKeyboardButton("◀️ Назад", callback_data="shop")],
    ])
    try:
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")
    except:
        await query.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")

# ─── CALLBACK HANDLER ────────────────────────────────────────────────────────
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cb = query.data
    user = query.from_user

    # Соглашение
    if cb == "agree":
        u = get_user(user.id)
        u["agreed"] = True
        save_data(data)
        await show_shop_query(query, context)
        return

    if cb == "disagree":
        await query.edit_message_text(
            "😔 Без принятия соглашения бот недоступен.\nНажми /start чтобы попробовать снова."
        )
        return

    if cb == "shop":
        await show_shop_query(query, context)
        return

    # Карточка товара
    if cb.startswith("view_"):
        idx = int(cb.split("_")[1])
        if idx >= len(data["products"]):
            await show_shop_query(query, context)
            return
        p = data["products"][idx]
        text = product_card_text(p)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 Купить", callback_data=f"buy_{idx}")],
            [InlineKeyboardButton("◀️ Назад в каталог", callback_data="shop")],
        ])
        if p.get("photo_file_id"):
            try:
                await query.message.delete()
            except:
                pass
            await query.message.chat.send_photo(
                photo=p["photo_file_id"], caption=text,
                reply_markup=keyboard, parse_mode="HTML"
            )
        else:
            try:
                await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")
            except:
                await query.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")
        return

    # Профиль
    if cb == "profile":
        u = get_user(user.id)
        await query.edit_message_text(
            f"👤 <b>Ваш профиль</b>\n\n"
            f"💰 Баланс: <b>{u.get('balance', 0)}₽</b>\n"
            f"👥 Приглашено: {u.get('referrals', 0)} чел. (+{REF_BONUS}₽ за каждого)\n",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 Пополнить баланс", callback_data="topup_menu")],
                [InlineKeyboardButton("◀️ Назад", callback_data="shop")],
            ]),
            parse_mode="HTML"
        )
        return

    # Реферал
    if cb == "referral":
        bot_info = await context.bot.get_me()
        link = f"https://t.me/{bot_info.username}?start=ref{user.id}"
        await query.edit_message_text(
            f"🔗 <b>Реферальная ссылка</b>\n\n<code>{link}</code>\n\n"
            f"За каждого приглашённого друга — {REF_BONUS}₽ сразу на баланс!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="shop")]]),
            parse_mode="HTML"
        )
        return

    # Меню пополнения
    if cb == "topup_menu":
        await show_topup_menu(query, context)
        return

    # Пополнение картой
    if cb == "topup_card":
        context.user_data["state"] = "topup_amount"
        try:
            await query.edit_message_text(
                f"🏦 <b>Пополнение через карту</b>\n\n"
                f"Введите сумму пополнения (минимум 10₽):",
                parse_mode="HTML"
            )
        except:
            await query.message.reply_text(
                f"🏦 <b>Пополнение через карту</b>\n\nВведите сумму пополнения (минимум 10₽):",
                parse_mode="HTML"
            )
        return

    # Пополнение звёздами (меню)
    if cb == "topup_stars":
        context.user_data["state"] = "topup_stars_amount"
        try:
            await query.edit_message_text(
                f"⭐️ <b>Пополнение звёздами</b>\n\n"
                f"Курс: 1 звезда = {STARS_RATE}₽\n\n"
                f"Введите сумму в рублях (минимум 10₽):",
                parse_mode="HTML"
            )
        except:
            await query.message.reply_text(
                f"⭐️ <b>Пополнение звёздами</b>\n\nКурс: 1 звезда = {STARS_RATE}₽\n\nВведите сумму в рублях (минимум 10₽):",
                parse_mode="HTML"
            )
        return

    # Купить товар
    if cb.startswith("buy_"):
        idx = int(cb.split("_")[1])
        context.user_data["buying_product"] = idx
        p = data["products"][idx]
        if p.get("digital"):
            # Цифровой — без доставки, сразу к оплате
            order_id = len(data["orders"]) + 1
            order = {
                "id": order_id, "user_id": user.id,
                "username": user.username or user.first_name,
                "product": p["name"], "price": p["price"],
                "delivery": False, "address": None,
                "status": "awaiting_payment",
                "timestamp": datetime.now().strftime("%d.%m.%Y %H:%M")
            }
            data["orders"].append(order)
            save_data(data)
            context.user_data["current_order_id"] = order_id
            await ask_payment(query, context, order_id)
        else:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🚶 Самовывоз", callback_data="delivery_no")],
                [InlineKeyboardButton("🏠 Доставка (+5₽)", callback_data="delivery_yes")],
                [InlineKeyboardButton("◀️ Назад", callback_data=f"view_{idx}")],
            ])
            text = f"📦 <b>{p['name']}</b> — {p['price']}₽\n\nКак получить товар?"
            try:
                await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")
            except:
                await query.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")
        return

    # Самовывоз
    if cb == "delivery_no":
        idx = context.user_data.get("buying_product", 0)
        p = data["products"][idx]
        order_id = len(data["orders"]) + 1
        order = {
            "id": order_id, "user_id": user.id,
            "username": user.username or user.first_name,
            "product": p["name"], "price": p["price"],
            "delivery": False, "address": None,
            "status": "awaiting_payment",
            "timestamp": datetime.now().strftime("%d.%m.%Y %H:%M")
        }
        data["orders"].append(order)
        save_data(data)
        context.user_data["current_order_id"] = order_id
        await ask_payment(query, context, order_id)
        return

    # Доставка
    if cb == "delivery_yes":
        idx = context.user_data.get("buying_product", 0)
        p = data["products"][idx]
        order_id = len(data["orders"]) + 1
        order = {
            "id": order_id, "user_id": user.id,
            "username": user.username or user.first_name,
            "product": p["name"], "price": p["price"] + DELIVERY_PRICE,
            "delivery": True, "address": None,
            "status": "awaiting_address",
            "timestamp": datetime.now().strftime("%d.%m.%Y %H:%M")
        }
        data["orders"].append(order)
        save_data(data)
        context.user_data["current_order_id"] = order_id
        context.user_data["state"] = "delivery_address"
        try:
            await query.edit_message_text("📍 Введите адрес доставки (кабинет / корпус / место):")
        except:
            await query.message.reply_text("📍 Введите адрес доставки (кабинет / корпус / место):")
        return

    # Оплата с баланса
    if cb.startswith("pay_balance_"):
        order_id = int(cb.split("_")[2])
        order = next((o for o in data["orders"] if o["id"] == order_id), None)
        if not order:
            return
        u = get_user(user.id)
        balance = u.get("balance", 0)
        price = order["price"]

        if balance >= price:
            u["balance"] -= price
            order["status"] = "pending_confirmation"
            order["payment_method"] = "balance"
            save_data(data)
            await _notify_admin_order(context, order_id, "с баланса")
            try:
                await query.edit_message_text("⏳ Оплата с баланса отправлена на подтверждение. Ожидайте!")
            except:
                await query.message.reply_text("⏳ Оплата с баланса отправлена на подтверждение. Ожидайте!")
        else:
            # Частичная — списываем что есть, остаток переводом
            used = balance
            remaining = price - used
            u["balance"] = 0
            order["partial_balance"] = used
            order["status"] = "awaiting_payment"
            save_data(data)
            text = (
                f"💳 <b>Частичная оплата</b>\n\n"
                f"С баланса списано: <b>{used}₽</b>\n"
                f"Осталось доплатить: <b>{remaining}₽</b>\n\n"
                f"Переведите <b>{remaining}₽</b> на карту Т-Банк:\n"
                f"<code>{CARD_NUMBER}</code>\n\n"
                f"В комментарии: <b>@{order['username']}</b>"
            )
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Я отправил деньги", callback_data=f"paid_{order_id}")]])
            try:
                await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")
            except:
                await query.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")
        return

    # Оплата звёздами (заказ)
    if cb.startswith("pay_stars_"):
        order_id = int(cb.split("_")[2])
        order = next((o for o in data["orders"] if o["id"] == order_id), None)
        if not order:
            return
        stars = order["price"] * STARS_RATE
        context.user_data["stars_order_id"] = order_id
        await context.bot.send_invoice(
            chat_id=user.id,
            title=f"Заказ #{order_id}: {order['product']}",
            description=f"Оплата заказа на {order['price']}₽ звёздами Telegram",
            payload=f"order_{order_id}",
            currency="XTR",
            prices=[LabeledPrice(label=order["product"], amount=stars)],
        )
        try:
            await query.edit_message_text("⭐️ Счёт на оплату звёздами отправлен выше.")
        except:
            pass
        return

    # Отправил деньги (перевод)
    if cb.startswith("paid_"):
        order_id = int(cb.split("_")[1])
        for o in data["orders"]:
            if o["id"] == order_id:
                o["status"] = "pending_confirmation"
                break
        save_data(data)
        await _notify_admin_order(context, order_id, "переводом на карту")
        try:
            await query.edit_message_text("⏳ Платёж отправлен на проверку. Ожидайте подтверждения!")
        except:
            await query.message.reply_text("⏳ Платёж отправлен на проверку. Ожидайте подтверждения!")
        return

    # ─── ТОЛЬКО АДМИН ────────────────────────────────────────────────────────
    if not is_admin(user.id):
        return

    # Тип товара при добавлении
    if cb == "item_type_physical":
        context.user_data["new_item"]["digital"] = False
        context.user_data["state"] = "add_photo"
        await query.edit_message_text(
            "📷 Отправьте <b>фото</b> товара\nили нажмите кнопку чтобы пропустить:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⏭ Пропустить (без фото)", callback_data="admin_skip_photo")]
            ]),
            parse_mode="HTML"
        )
        return

    if cb == "item_type_digital":
        context.user_data["new_item"]["digital"] = True
        context.user_data["state"] = "add_digital_ask"
        await query.edit_message_text(
            "💻 <b>Цифровой товар</b>\n\n"
            "Что отправить покупателю после оплаты?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📝 Текст (ответы, коды...)", callback_data="digital_send_text")],
                [InlineKeyboardButton("📄 Файл (PDF, Word...)", callback_data="digital_send_file")],
                [InlineKeyboardButton("🖼 Фото (скрин ответов)", callback_data="digital_send_photo")],
            ]),
            parse_mode="HTML"
        )
        return

    if cb == "digital_send_text":
        context.user_data["state"] = "add_digital_text"
        await query.edit_message_text(
            "📝 Введите текст который получит покупатель после оплаты:\n"
            "(ответы, ссылка, код доступа и т.д.)",
        )
        return

    if cb == "digital_send_file":
        context.user_data["new_item"]["digital_type"] = "file"
        context.user_data["state"] = "add_digital_file"
        await query.edit_message_text(
            "📄 Отправьте <b>файл</b> который получит покупатель после оплаты:\n"
            "(PDF, Word, любой документ)",
            parse_mode="HTML"
        )
        return

    if cb == "digital_send_photo":
        context.user_data["new_item"]["digital_type"] = "photo"
        context.user_data["state"] = "add_digital_photo"
        await query.edit_message_text(
            "🖼 Отправьте <b>фото</b> которое получит покупатель после оплаты:\n"
            "(скрин ответов, решение задач и т.д.)",
            parse_mode="HTML"
        )
        return

    if cb == "admin_add":
        context.user_data["state"] = "add_name"
        context.user_data["new_item"] = {}
        await query.edit_message_text("📝 Введите <b>название</b> товара:", parse_mode="HTML")
        return

    if cb == "admin_skip_photo":
        item = context.user_data.get("new_item", {})
        item["photo_file_id"] = None
        data["products"].append(item)
        save_data(data)
        context.user_data["state"] = None
        await query.edit_message_text(
            f"✅ Товар <b>«{item['name']}»</b> добавлен!\n"
            f"Кол-во: {item['count']} шт., цена: {item['price']}₽",
            reply_markup=admin_keyboard(), parse_mode="HTML"
        )
        return

    if cb == "admin_products":
        if not data["products"]:
            await query.edit_message_text("Нет товаров.", reply_markup=admin_keyboard())
            return
        text = "🗂 <b>Список товаров:</b>\n\n"
        keyboard = []
        for i, p in enumerate(data["products"]):
            icon = "💻" if p.get("digital") else ("🖼" if p.get("photo_file_id") else "📦")
            text += f"{i+1}. {icon} {p['name']} — {p['price']}₽, {p['count']} шт.\n"
            keyboard.append([InlineKeyboardButton(f"🗑 Удалить «{p['name']}»", callback_data=f"del_product_{i}")])
        keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="admin_back")])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return

    if cb.startswith("del_product_"):
        idx = int(cb.split("_")[2])
        del data["products"][idx]
        save_data(data)
        await query.edit_message_text("✅ Товар удалён.", reply_markup=admin_keyboard())
        return

    # Заявки на покупку
    if cb == "admin_orders":
        pending = [o for o in data["orders"] if o["status"] == "pending_confirmation" and o.get("type") != "topup"]
        if not pending:
            await query.edit_message_text("📭 Нет новых заявок на покупку.", reply_markup=admin_keyboard())
            return
        text = f"📋 <b>Заявок на проверке: {len(pending)}</b>\n\n"
        keyboard = []
        for o in pending:
            method = o.get("payment_method", "перевод")
            partial = f" (баланс: {o.get('partial_balance', 0)}₽)" if o.get("partial_balance") else ""
            text += (
                f"<b>#{o['id']}</b> @{o['username']} — {o['product']} ({o['price']}₽)\n"
                f"💳 {method}{partial}\n"
                f"{'🚚 ' + str(o.get('address')) if o['delivery'] else '🚶 Самовывоз'}\n"
                f"⏰ {o['timestamp']}\n\n"
            )
            keyboard.append([
                InlineKeyboardButton(f"✅ #{o['id']}", callback_data=f"admin_confirm_{o['id']}"),
                InlineKeyboardButton(f"❌ #{o['id']}", callback_data=f"admin_reject_{o['id']}"),
                InlineKeyboardButton(f"🗑 #{o['id']}", callback_data=f"admin_del_order_{o['id']}"),
            ])
        keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="admin_back")])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return

    # Заявки на пополнение
    if cb == "admin_topup_orders":
        pending = [o for o in data["orders"] if o["status"] == "pending_confirmation" and o.get("type") == "topup"]
        if not pending:
            await query.edit_message_text("📭 Нет заявок на пополнение.", reply_markup=admin_keyboard())
            return
        text = f"💳 <b>Заявок на пополнение: {len(pending)}</b>\n\n"
        keyboard = []
        for o in pending:
            text += (
                f"<b>#{o['id']}</b> @{o['username']}\n"
                f"💰 Сумма: {o['price']}₽\n"
                f"⏰ {o['timestamp']}\n\n"
            )
            keyboard.append([
                InlineKeyboardButton(f"✅ #{o['id']}", callback_data=f"admin_topup_confirm_{o['id']}"),
                InlineKeyboardButton(f"❌ #{o['id']}", callback_data=f"admin_topup_reject_{o['id']}"),
                InlineKeyboardButton(f"🗑 #{o['id']}", callback_data=f"admin_del_order_{o['id']}"),
            ])
        keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="admin_back")])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return

    # Удалить заявку
    if cb.startswith("admin_del_order_"):
        order_id = int(cb.split("_")[3])
        data["orders"] = [o for o in data["orders"] if o["id"] != order_id]
        save_data(data)
        await query.edit_message_text("🗑 Заявка удалена.", reply_markup=admin_keyboard())
        return

    # Подтвердить покупку
    if cb.startswith("admin_confirm_"):
        order_id = int(cb.split("_")[2])
        order = next((o for o in data["orders"] if o["id"] == order_id), None)
        if not order:
            return

        # Найти товар и проверить цифровой ли он
        product = next((p for p in data["products"] if p["name"] == order["product"]), None)
        if product and product.get("digital"):
            # Цифровой — отправляем контент автоматически
            buyer_id = order["user_id"]
            order["status"] = "confirmed"
            save_data(data)
            try:
                dtype = product.get("digital_type", "text")
                if dtype == "text":
                    await context.bot.send_message(
                        buyer_id,
                        f"✅ Оплата подтверждена! Вот ваш товар «{product['name']}»:\n\n"
                        f"<code>{product['digital_text']}</code>",
                        parse_mode="HTML"
                    )
                elif dtype == "file":
                    await context.bot.send_document(
                        buyer_id,
                        document=product["digital_file_id"],
                        caption=f"✅ Оплата подтверждена! Ваш товар «{product['name']}»"
                    )
                elif dtype == "photo":
                    await context.bot.send_photo(
                        buyer_id,
                        photo=product["digital_photo_id"],
                        caption=f"✅ Оплата подтверждена! Ваш товар «{product['name']}»"
                    )
            except Exception as e:
                logger.error(f"Ошибка отправки цифрового товара: {e}")
            await query.edit_message_text(
                f"✅ Заказ #{order_id} подтверждён — цифровой товар отправлен автоматически.",
                reply_markup=admin_keyboard()
            )
        else:
            # Физический — спрашиваем где забрать
            context.user_data["confirm_order_id"] = order_id
            context.user_data["state"] = "admin_send_location"
            await query.edit_message_text(
                "📍 Напишите где забрать товар или адрес доставки (уйдёт покупателю).\n\n"
                "Можно также отправить <b>фото</b> (например, место получения):",
                parse_mode="HTML"
            )
        return

    # Отклонить покупку
    if cb.startswith("admin_reject_"):
        order_id = int(cb.split("_")[2])
        buyer_id = None
        for o in data["orders"]:
            if o["id"] == order_id:
                o["status"] = "rejected"
                buyer_id = o["user_id"]
                # Возврат баланса если была частичная оплата
                partial = o.get("partial_balance", 0)
                if partial > 0:
                    u = get_user(buyer_id)
                    u["balance"] = u.get("balance", 0) + partial
                break
        save_data(data)
        if buyer_id:
            try:
                await context.bot.send_message(buyer_id, "❌ Ваш платёж не подтверждён. Напишите @omunv.")
            except:
                pass
        await query.edit_message_text("❌ Заявка отклонена.", reply_markup=admin_keyboard())
        return

    # Подтвердить пополнение
    if cb.startswith("admin_topup_confirm_"):
        order_id = int(cb.split("_")[3])
        buyer_id = None
        amount = 0
        for o in data["orders"]:
            if o["id"] == order_id:
                o["status"] = "confirmed"
                buyer_id = o["user_id"]
                amount = o["price"]
                u = get_user(buyer_id)
                u["balance"] = u.get("balance", 0) + amount
                break
        save_data(data)
        if buyer_id:
            try:
                await context.bot.send_message(
                    buyer_id,
                    f"✅ Баланс пополнен на <b>{amount}₽</b>!\n"
                    f"Текущий баланс: <b>{get_user(buyer_id)['balance']}₽</b>",
                    parse_mode="HTML"
                )
            except:
                pass
        await query.edit_message_text(f"✅ Пополнение на {amount}₽ подтверждено.", reply_markup=admin_keyboard())
        return

    # Отклонить пополнение
    if cb.startswith("admin_topup_reject_"):
        order_id = int(cb.split("_")[3])
        buyer_id = None
        for o in data["orders"]:
            if o["id"] == order_id:
                o["status"] = "rejected"
                buyer_id = o["user_id"]
                break
        save_data(data)
        if buyer_id:
            try:
                await context.bot.send_message(buyer_id, "❌ Пополнение не подтверждено. Напишите @omunv.")
            except:
                pass
        await query.edit_message_text("❌ Пополнение отклонено.", reply_markup=admin_keyboard())
        return

    if cb == "admin_back":
        await query.edit_message_text("🔐 Админ-панель:", reply_markup=admin_keyboard())
        return

# ─── УВЕДОМЛЕНИЕ АДМИНА О ЗАЯВКЕ ─────────────────────────────────────────────
async def _notify_admin_order(context, order_id, method_label):
    admin_id = data.get("admin_id")
    if not admin_id:
        return
    o = next((o for o in data["orders"] if o["id"] == order_id), None)
    if not o:
        return
    text = (
        f"🛒 <b>Новая заявка #{order_id}</b>\n"
        f"👤 @{o['username']}\n"
        f"📦 {o['product']}\n"
        f"💰 {o['price']}₽ ({method_label})\n"
        f"{'🚚 Доставка → ' + str(o.get('address')) if o['delivery'] else '🚶 Самовывоз'}\n"
        f"⏰ {o['timestamp']}"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Подтвердить", callback_data=f"admin_confirm_{order_id}")],
        [InlineKeyboardButton("❌ Отклонить", callback_data=f"admin_reject_{order_id}")],
        [InlineKeyboardButton("🗑 Удалить", callback_data=f"admin_del_order_{order_id}")],
    ])
    await context.bot.send_message(admin_id, text, reply_markup=keyboard, parse_mode="HTML")

# ─── ОПЛАТА ЗВЁЗДАМИ (pre_checkout + successful_payment) ──────────────────────
async def pre_checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    await query.answer(ok=True)

async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    payload = payment.invoice_payload
    user = update.effective_user

    if payload.startswith("order_"):
        order_id = int(payload.split("_")[1])
        for o in data["orders"]:
            if o["id"] == order_id:
                o["status"] = "pending_confirmation"
                o["payment_method"] = "звёзды"
                break
        save_data(data)
        await _notify_admin_order(context, order_id, "звёздами Telegram")
        await update.message.reply_text(
            f"⭐️ Оплата звёздами получена! Заявка #{order_id} отправлена на подтверждение."
        )

    elif payload.startswith("topup_"):
        amount = int(payload.split("_")[1])
        order_id = len(data["orders"]) + 1
        u = get_user(user.id)
        u["balance"] = u.get("balance", 0) + amount
        order = {
            "id": order_id, "user_id": user.id,
            "username": user.username or user.first_name,
            "product": f"Пополнение баланса", "price": amount,
            "delivery": False, "address": None,
            "type": "topup", "status": "confirmed",
            "payment_method": "звёзды",
            "timestamp": datetime.now().strftime("%d.%m.%Y %H:%M")
        }
        data["orders"].append(order)
        save_data(data)
        await update.message.reply_text(
            f"⭐️ Баланс пополнен на <b>{amount}₽</b>!\n"
            f"Текущий баланс: <b>{u['balance']}₽</b>",
            parse_mode="HTML"
        )

# ─── ТЕКСТОВЫЕ СООБЩЕНИЯ ─────────────────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    user = update.effective_user

    if text == ADMIN_CODE:
        data["admin_id"] = user.id
        save_data(data)
        await update.message.reply_text("🔐 Добро пожаловать в админ-панель!", reply_markup=admin_keyboard())
        return

    state = context.user_data.get("state")

    # ── Добавление товара ──
    if state == "add_name":
        context.user_data["new_item"] = {"name": text}
        context.user_data["state"] = "add_desc"
        await update.message.reply_text(
            "📝 Введите <b>описание</b> товара\n(или напишите <code>-</code> чтобы пропустить):",
            parse_mode="HTML"
        )
        return

    if state == "add_desc":
        context.user_data["new_item"]["description"] = "" if text == "-" else text
        context.user_data["state"] = "add_count"
        await update.message.reply_text("🔢 Введите <b>количество</b> товара:", parse_mode="HTML")
        return

    if state == "add_count":
        try:
            context.user_data["new_item"]["count"] = int(text)
            context.user_data["state"] = "add_price"
            await update.message.reply_text("💰 Введите <b>цену</b> в рублях:", parse_mode="HTML")
        except ValueError:
            await update.message.reply_text("⚠️ Введите целое число!")
        return

    if state == "add_price":
        try:
            context.user_data["new_item"]["price"] = int(text)
            context.user_data["state"] = "add_type"
            await update.message.reply_text(
                "📦 Выберите <b>тип товара</b>:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📦 Физический (тетрадь, ручка...)", callback_data="item_type_physical")],
                    [InlineKeyboardButton("💻 Цифровой (ответы, файл...)", callback_data="item_type_digital")],
                ]),
                parse_mode="HTML"
            )
        except ValueError:
            await update.message.reply_text("⚠️ Введите целое число!")
        return

    # ── Цифровой товар: текстовый контент ──
    if state == "add_digital_text":
        item = context.user_data.get("new_item", {})
        item["digital_text"] = text
        item["digital_type"] = "text"
        data["products"].append(item)
        save_data(data)
        context.user_data["state"] = None
        await update.message.reply_text(
            f"✅ Цифровой товар <b>«{item['name']}»</b> добавлен!\n"
            f"После оплаты покупатель получит текст автоматически.\n"
            f"Цена: {item['price']}₽",
            reply_markup=admin_keyboard(), parse_mode="HTML"
        )
        return

    # ── Адрес доставки ──
    if state == "delivery_address":
        order_id = context.user_data.get("current_order_id")
        for o in data["orders"]:
            if o["id"] == order_id:
                o["address"] = text
                break
        save_data(data)
        context.user_data["state"] = None
        await ask_payment(update, context, order_id)
        return

    # ── Подтверждение заказа (текст) ──
    if state == "admin_send_location":
        order_id = context.user_data.get("confirm_order_id")
        buyer_id = None
        for o in data["orders"]:
            if o["id"] == order_id:
                o["location"] = text
                o["status"] = "confirmed"
                buyer_id = o["user_id"]
                break
        save_data(data)
        context.user_data["state"] = None
        if buyer_id:
            try:
                await context.bot.send_message(buyer_id, f"✅ Ваш заказ подтверждён!\n📍 {text}")
            except:
                pass
        await update.message.reply_text("✅ Заказ подтверждён, покупатель уведомлён.", reply_markup=admin_keyboard())
        return

    # ── Сумма пополнения картой ──
    if state == "topup_amount":
        try:
            amount = int(text)
            if amount < 10:
                await update.message.reply_text("⚠️ Минимум 10₽.")
                return
            order_id = len(data["orders"]) + 1
            order = {
                "id": order_id, "user_id": user.id,
                "username": user.username or user.first_name,
                "product": "Пополнение баланса", "price": amount,
                "delivery": False, "address": None,
                "type": "topup", "status": "pending_confirmation",
                "payment_method": "карта",
                "timestamp": datetime.now().strftime("%d.%m.%Y %H:%M")
            }
            data["orders"].append(order)
            save_data(data)
            context.user_data["state"] = None

            # Уведомить админа
            admin_id = data.get("admin_id")
            if admin_id:
                await context.bot.send_message(
                    admin_id,
                    f"💳 <b>Заявка на пополнение #{order_id}</b>\n"
                    f"👤 @{order['username']}\n"
                    f"💰 {amount}₽\n"
                    f"⏰ {order['timestamp']}",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("✅ Подтвердить", callback_data=f"admin_topup_confirm_{order_id}")],
                        [InlineKeyboardButton("❌ Отклонить", callback_data=f"admin_topup_reject_{order_id}")],
                        [InlineKeyboardButton("🗑 Удалить", callback_data=f"admin_del_order_{order_id}")],
                    ]),
                    parse_mode="HTML"
                )

            await update.message.reply_text(
                f"💳 <b>Пополнение на {amount}₽</b>\n\n"
                f"Переведите <b>{amount}₽</b> на карту Т-Банк:\n"
                f"<code>{CARD_NUMBER}</code>\n\n"
                f"В комментарии укажите: <b>@{order['username']}</b>\n"
                f"Если не можете — напишите @omunv и скиньте чек.\n\n"
                f"После перевода ожидайте подтверждения от администратора.",
                parse_mode="HTML"
            )
        except ValueError:
            await update.message.reply_text("⚠️ Введите целое число!")
        return

    # ── Сумма пополнения звёздами ──
    if state == "topup_stars_amount":
        try:
            amount = int(text)
            if amount < 10:
                await update.message.reply_text("⚠️ Минимум 10₽.")
                return
            stars = amount * STARS_RATE
            context.user_data["state"] = None
            await context.bot.send_invoice(
                chat_id=user.id,
                title=f"Пополнение баланса на {amount}₽",
                description=f"Пополнение баланса в школьном магазине",
                payload=f"topup_{amount}",
                currency="XTR",
                prices=[LabeledPrice(label="Пополнение", amount=stars)],
            )
            await update.message.reply_text(f"⭐️ Счёт на {stars} звёзд отправлен выше.")
        except ValueError:
            await update.message.reply_text("⚠️ Введите целое число!")
        return

# ─── ФОТО ────────────────────────────────────────────────────────────────────
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    state = context.user_data.get("state")

    # Цифровой товар — фото как контент
    if state == "add_digital_photo" and is_admin(user.id):
        file_id = update.message.photo[-1].file_id
        item = context.user_data.get("new_item", {})
        item["digital_photo_id"] = file_id
        item["digital_type"] = "photo"
        data["products"].append(item)
        save_data(data)
        context.user_data["state"] = None
        await update.message.reply_text(
            f"✅ Цифровой товар <b>«{item['name']}»</b> добавлен!\n"
            f"После оплаты покупатель получит это фото автоматически.\n"
            f"Цена: {item['price']}₽",
            reply_markup=admin_keyboard(), parse_mode="HTML"
        )
        return

    # Добавление товара с фото (физический)
    if state == "add_photo" and is_admin(user.id):
        file_id = update.message.photo[-1].file_id
        item = context.user_data.get("new_item", {})
        item["photo_file_id"] = file_id
        data["products"].append(item)
        save_data(data)
        context.user_data["state"] = None
        await update.message.reply_text(
            f"✅ Товар <b>«{item['name']}»</b> добавлен с фото!\n"
            f"Кол-во: {item['count']} шт., цена: {item['price']}₽",
            reply_markup=admin_keyboard(), parse_mode="HTML"
        )
        return

    # Подтверждение заказа с фото (вместо текста)
    if state == "admin_send_location" and is_admin(user.id):
        order_id = context.user_data.get("confirm_order_id")
        buyer_id = None
        for o in data["orders"]:
            if o["id"] == order_id:
                o["status"] = "confirmed"
                buyer_id = o["user_id"]
                break
        save_data(data)
        context.user_data["state"] = None
        if buyer_id:
            try:
                file_id = update.message.photo[-1].file_id
                caption = update.message.caption or "✅ Ваш заказ подтверждён!"
                await context.bot.send_photo(
                    buyer_id,
                    photo=file_id,
                    caption=f"✅ Заказ #{order_id} подтверждён!\n{caption}"
                )
            except:
                pass
        await update.message.reply_text("✅ Заказ подтверждён с фото, покупатель уведомлён.", reply_markup=admin_keyboard())
        return

# ─── ДОКУМЕНТЫ ──────────────────────────────────────────────────────────────
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    state = context.user_data.get("state")
    if state == "add_digital_file" and is_admin(user.id):
        file_id = update.message.document.file_id
        item = context.user_data.get("new_item", {})
        item["digital_file_id"] = file_id
        item["digital_type"] = "file"
        data["products"].append(item)
        save_data(data)
        context.user_data["state"] = None
        await update.message.reply_text(
            f"✅ Цифровой товар <b>«{item['name']}»</b> добавлен с файлом!\n"
            f"После оплаты покупатель получит этот файл автоматически.\n"
            f"Цена: {item['price']}₽",
            reply_markup=admin_keyboard(), parse_mode="HTML"
        )

# ─── ЗАПУСК ──────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("🤖 Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()