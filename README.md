# Каталог аддонов MajorDoMo

Страница со всеми аддонами MajorDoMo Connect: иконки, системные имена модулей,
авторы, категории, описания и число установок. Есть поиск, фильтр по категории
и сортировка.

Страницу собирает `build_addons_table.py`. GitHub Actions запускает его каждый
день и публикует результат на GitHub Pages:

- `index.html` — каталог;
- `addons.json` — те же данные в JSON.

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
