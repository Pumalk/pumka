# Полезные команды Pumka (шпаргалка)

## Дамп всего кода core/ в файл (для отправки исполнителю без обрезки)
cd ~/Pumka && for f in core/*.py; do echo "=== $f ==="; cat "$f"; echo; done > core_dump.txt

## Проверить, что дамп не пустой
wc -l core_dump.txt

## Активация окружения
cd ~/Pumka && source venv/bin/activate

## Проверка здоровья
python -m core.health_check

## Запуск Telegram-бота
python -m interfaces.telegram.bot

## Демо-чат (консоль)
python scripts/demo_chat.py

## Git (точные команды коммитов даёт исполнитель этапа)
git add -A
git commit -m "сообщение"
git tag etap-N-gotov
