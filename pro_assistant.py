"""Deterministic private assistant for work time and pay.

Deliberately no third-party LLM API calls, prompts, or external transmissions.
The rules cite only values already stored in the private SQLite database.
"""
from datetime import datetime, timedelta
from html import escape
from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup
from core import TZ, totals, shifts, paid_hours, week_totals, rate, local_day
from finance import payment_totals, integrity_summary

def menu():
    return InlineKeyboardMarkup([
      [B("💰 Сколько заработал?",callback_data="helper:pay"),
       B("📅 Эта неделя",callback_data="helper:week")],
      [B("⚠️ Что проверить?",callback_data="helper:issues"),
       B("🏢 Офис и мои часы",callback_data="helper:office")],
      [B("📂 Выгрузить отчёт",callback_data="ui:reports"),
       B("🏠 Главная",callback_data="ui:home")]])

def intro():
    return ("🧭 <b>Мой помощник</b>\n\n"
        "Я могу быстро разобраться в заработке, сменах, перерывах и сверке с офисом.\n"
        "Задай вопрос обычным сообщением или выбери одну из кнопок.\n\n"
        "🔐 <i>Работаю локально на сервере: данные никуда дополнительно не отправляю. "
        "Подсказки строятся на сохранённых записях и правилах расчёта.</i>")

def answer(db,topic):
    month=datetime.now(TZ).strftime("%Y-%m")
    t=totals(db,month)
    payouts=payment_totals(db,month)
    if topic=="pay":
        return (f"💰 <b>Начисления за {month}</b>\n\n"
           f"Отработано: {t['hours']:.2f} ч, закрыто смен: {t['shifts']}.\n"
           f"По личному учёту: <b>{t['earnings']:,.2f} ₽</b>.\n"
           f"Отмечено поступлений: {sum(payouts.values()):,.2f} ₽.\n"
           "Начисление и поступление могут отличаться из-за НДФЛ, авансов и правил бухгалтерии.")
    if topic=="week":
        monday,h,earn,completed=week_totals(db)
        return (f"📅 <b>Неделя с {monday:%d.%m.%Y}</b>\n\n"
              f"Закрыто: {completed} смен\n"
              f"Часов: {h:.2f}\n"
              f"Начислено: {earn:,.2f} ₽\n"
              "Незавершённые смены пока не засчитаны.")
    if topic=="issues":
        s=integrity_summary(db)
        dates=sorted({local_day(r["start_utc"]) for r in shifts(db,limit=20)})
        recent=db.execute("SELECT COUNT(*) FROM office_image_days").fetchone()[0]
        lines=["⚠️ <b>Проверка данных</b>\n"]
        lines.append(f"• Не закрыто смен: <b>{s['open']}</b>")
        lines.append(f"• Некорректных интервалов: <b>{s['invalid_dates']}</b>")
        lines.append(f"• Офисных дат в фото: {recent}; подтверждённых часов: {s['office']}")
        if not s["office"]:lines.append("• Для окончательной сверки нужно загрузить исходный офисный XLSX.")
        lines.append("\n<i>Сырые скриншоты не засчитываются второй раз как отработанные смены.</i>")
        return "\n".join(lines)
    if topic=="office":
        dates=db.execute("SELECT COUNT(*) FROM office_days").fetchone()[0]
        photo=db.execute("SELECT COUNT(*) FROM office_image_days").fetchone()[0]
        return (f"🏢 <b>Сверка с офисом</b>\n\n"
                f"Точные офисные часы: <b>{dates}</b> дат.\n"
                f"Зарегистрированы даты фото: {photo}.\n"
                "Чтобы сравнить выплаты, загрузи исходный офисный XLSX через «Сверка» → «Загрузить файл».\n"
                "<i>Не подставляю догадки вместо офисных часов.</i>")
    if topic=="break":
        return ("☕ <b>Перерывы</b>\n\n"
                "Время между приходом и уходом уменьшается на указанный перерыв. "
                "Для архивных файлов сохраняются их исходные значения. "
                "Если в офисном табеле перерыв уже исключён, повторно вычитать его нельзя.")
    if topic=="rates":
        return (f"💵 <b>Почасовая ставка</b>\n\n"
                f"Сегодня: {rate(db):,.2f} ₽/час.\n"
                "Изменение ставки планируется с будущей даты: прошлые начисления не пересчитываются.")
    return ("🧭 <b>Помогу с учётом работы</b>\n\n"
      "Спроси: «сколько заработал», «эта неделя», «почему не совпадают часы», "
      "«какие проблемы», «перерывы», «моя ставка».\n\n"
      "Для сложных вопросов нужна сверка с документами работодателя.")

def detect(text):
    t=text.lower().strip()
    if any(s in t for s in ["заработ","зарплат","деньг","получ","выплат","начисл"]):return "pay"
    if any(s in t for s in ["недел","неделю","семь дн","7 дн"]):return "week"
    if any(s in t for s in ["проблем","ошиб","провер","не хватает","забыл","потерял"]):return "issues"
    if any(s in t for s in ["офис","табел","расхожд","свер","не совпад"]):return "office"
    if any(s in t for s in ["перерыв","обед","пауза"]):return "break"
    if any(s in t for s in ["ставк","тариф","час стоит"]):return "rates"
    return "help"
