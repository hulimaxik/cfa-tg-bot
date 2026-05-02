# CFA TG Bot — минимальный деплой

## 1. Создать GitHub репо
- Зайди на github.com → New repository → имя `cfa-tg-bot` → Public → Create
- Загрузи 4 файла из этого архива: `main.py`, `Dockerfile`, `requirements.txt`, `.env.example`

## 2. Railway деплой
- railway.com → New Project → Deploy from GitHub repo → выбери `cfa-tg-bot`
- Railway сам найдёт Dockerfile и соберёт
- После деплоя: Settings → Variables → добавь все переменные из `.env.example`

## 3. Проверка
- В Telegram боту: /start → должен ответить
- /status → покажет статус из Supabase
- /stop → inline кнопка подтверждения

## 4. n8n (следующий шаг)
Когда бот работает — добавим n8n на Railway и workflow'ы.
