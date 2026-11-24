<div align="center">

<img src="assets/header.svg" width="100%" alt="Мои смены — рабочее время, график и выплаты">

# Мои смены

**Рабочее время, смены и деньги — в одном личном кабинете.**

Telegram-бот и адаптивная Mini App для телефона и компьютера. Быстрые отметки, календарь, финансовая аналитика и дополнительные настройки приватности.

**[Открыть бота в Telegram](https://t.me/MoiSmenyBot)** · **[Что нового](CHANGELOG.md)** · **[Версия 2026.11.24](docs/RELEASE_2026_11_24.md)**

![Python](https://img.shields.io/badge/Python-3.11%2B-1B4258?style=flat-square&logo=python&logoColor=white)
![Telegram](https://img.shields.io/badge/Telegram-Mini_App-287C96?style=flat-square&logo=telegram&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-WAL-396779?style=flat-square&logo=sqlite&logoColor=white)
![Linux](https://img.shields.io/badge/Linux-systemd-42694B?style=flat-square&logo=linux&logoColor=white)
![Version](https://img.shields.io/badge/Version-2026.11.24-C69856?style=flat-square)

</div>

---

## Интерфейс

<div align="center">

**Рабочий стол · Тёмная тема**

<img src="assets/screenshots/desktop-overview-dark.png" width="96%" alt="Рабочий стол: главная, смены и календарный план — демонстрационные данные">

**Финансы · Светлая тема**

<img src="assets/screenshots/desktop-finance-light.png" width="96%" alt="Рабочий стол: начисления, выплаты и динамика — демонстрационные данные">

</div>

### Мобильная версия

<table>
<tr>
<td align="center" width="50%"><img src="assets/screenshots/mobile-home-dark.png" width="280" alt="Главная мобильного кабинета"><br><b>Сегодня</b></td>
<td align="center" width="50%"><img src="assets/screenshots/mobile-calendar-dark.png" width="280" alt="Календарь рабочих смен"><br><b>Календарь</b></td>
</tr>
<tr>
<td align="center" width="50%"><img src="assets/screenshots/mobile-finance-dark.png" width="280" alt="Начисления и история выплат"><br><b>Деньги</b></td>
<td align="center" width="50%"><img src="assets/screenshots/mobile-settings-dark.png" width="280" alt="Настройки и дополнительные функции"><br><b>Ещё</b></td>
</tr>
</table>

<details>
<summary><b>Светлая тема и экран PIN-блокировки</b></summary>
<table>
<tr>
<td align="center"><img src="assets/screenshots/mobile-home-light.png" width="270" alt="Светлая тема Mini App"><br>Светлая тема</td>
<td align="center"><img src="assets/screenshots/mobile-pin-lock.png" width="270" alt="Демонстрационный экран PIN-блокировки"><br>Защита кабинета</td>
</tr>
</table>
</details>

<sub>На всех скриншотах — исключительно вымышленные записи. Реальные смены, выплаты, имена, документы и ключи не публикуются.</sub>

---

## Возможности

| Раздел | Что доступно |
|:--|:--|
| **Сегодня** | Приход и уход, живой таймер, перерывы, последние смены |
| **Календарь** | План и факт, выходные, ночные смены, исправления, экспорт в **.ics** |
| **Деньги** | Начислено, получено, остаток, ставки, премии, удержания и две части месяца |
| **История выплат** | Поиск по дате, сумме и комментарию; фильтры по рабочему месяцу и периоду |
| **Отчёты** | Excel, PDF, история изменений, сверка с офисными данными |
| **Ещё** | Быстрые действия, оформление, защита и расширенные инструменты |

### Приватность и безопасность

- **Переворот телефона.** Полное движение **экраном вверх → вниз → обратно вверх** скрывает суммы; повторный полный жест показывает их. Промежуточное положение ничего не переключает.
- **Автоскрытие.** Деньги закрываются после бездействия и сворачивания приложения.
- **PIN-код.** Добровольная дополнительная защита из шести цифр; сервер хранит только проверочный хеш, а не исходный код.
- **Биометрия.** Разблокировка через Telegram BiometricManager на совместимых устройствах; PIN остаётся резервным способом.
- **Восстановление и ограничения.** Одноразовый резервный код, ограничение неудачных попыток, защита от повторных записей и конфликтов редактирования.
- **Закрытые API.** Доступ владельца проверяется по подписи Telegram; при включённой защите дополнительно требуется сессия разблокировки.

PIN и биометрия **отключены по умолчанию** и включаются самим владельцем. PIN защищает Mini App, но не переписку Telegram и не ранее скачанные файлы. Финансовые расчёты справочные и не заменяют официальную ведомость работодателя.

---

## Запуск для разработчика

Понадобятся Python 3.11+, Node.js и собственный токен бота от [BotFather](https://t.me/BotFather).

~~~bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

mkdir -p data
chmod 700 data
export WORKSHIFTS_DATA="$PWD/data"

read -rsp "Telegram bot token: " TELEGRAM_BOT_TOKEN; echo
export TELEGRAM_BOT_TOKEN
python bot.py
~~~

Дополнительные функции Mini App требуют собственной HTTPS-среды, проверки подписи Telegram и настройки владельца. Без Telegram-авторизации интерфейс может показывать только демонстрационный режим.

### Тестирование

~~~bash
python -m pip install -r requirements-dev.txt
python scripts/run_server_checks.py

npm ci
npm run test:browser
npm run test:design

python scripts/check_public.py
~~~

| Проверка версии 2026.11.24 | Результат |
|---|---:|
| Изолированные серверные тесты | **454 / 454** |
| Браузерные сценарии | **267 / 267** |
| Визуальные проверки | **263 / 263** |
| Тесты PIN и биометрии | **12 / 12** |

Проверены семь ширин экрана — **320–1440 px**, обе темы и ошибки в типовых сценариях. Аппаратные жесты и биометрия зависят от устройства и требуют отдельной ручной проверки.

---

## Исходный код

**Основные модули:** <code>bot.py</code> — Telegram-бот, <code>core.py</code> — хранение смен, <code>finance.py</code> — выплаты, <code>miniapp/</code> — интерфейс, <code>miniapp_server.py</code> — закрытый API, <code>security_gate.py</code> — PIN/биометрия, <code>backup.py</code> — резервирование, <code>scripts/</code> и <code>test_*.py</code> — тесты.

[Архитектура](docs/ARCHITECTURE.md) · [Безопасность](docs/SECURITY.md) · [Список изменений](CHANGELOG.md) · [Примечания к версии](docs/RELEASE_2026_11_24.md)

<div align="center"><sub>Мои смены · 2026.11.24</sub></div>
