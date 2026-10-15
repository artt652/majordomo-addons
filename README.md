# Каталог аддонов MajorDoMo

Страница со всеми аддонами MajorDoMo Connect: иконки, системные имена модулей,
авторы, категории, описания, дата последнего обновления и число установок.
Есть поиск, фильтры по категории и публикации, сортировка.

Откуда берутся данные:

- **маркет** (`https://connect.majordomohome.com/market/?lang=ru&all=1`) — название,
  автор, категория, описание, дата и комментарий последнего обновления;
- **страницы аддонов** — иконка, число установок и модули, которых нет в маркете.
  Они помечаются «не опубликовано»;
- **GitHub** — последний коммит неопубликованных модулей.

Страницу собирает `build_addons_table.py`. GitHub Actions запускает его каждый
день и публикует результат на GitHub Pages:

- `index.html` — каталог;
- `addons.json` — итоговые данные каталога в JSON;
- `market.json` — исходный ответ маркета.

## Настройка

1. Загрузите в репозиторий `build_addons_table.py` и `.github/workflows/pages.yml`.
2. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
3. **Actions → «Каталог аддонов → GitHub Pages» → Run workflow.**

Через пару минут страница откроется по адресу
`https://<ваш-логин>.github.io/<имя-репозитория>/`.

## Запуск вручную

```
python3 build_addons_table.py              # аддоны 1–350 → addons_table.html
python3 build_addons_table.py --count 400  # другое количество
python3 build_addons_table.py --refresh    # перекачать всё
```
