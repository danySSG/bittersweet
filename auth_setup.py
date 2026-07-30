"""
auth_setup.py — разовая авторизация в YouTube Music для доступа к ТВОИМ лайкам/библиотеке.

Нужна ТОЛЬКО если хочешь тянуть личные лайки (плейлист "Мне понравилось"),
а не публичный плейлист. Для публичного плейлиста авторизация не нужна вообще.

Как получить заголовки (2 минуты):
  1. Открой https://music.youtube.com в браузере, залогинься.
  2. Открой инструменты разработчика: Cmd+Option+I → вкладка Network (Сеть).
  3. В фильтре набери:  /browse
  4. Обнови страницу или кликни по любому разделу — появятся запросы POST к
     music.youtube.com/youtubei/v1/browse
  5. Кликни по такому запросу → Headers → найди блок "Request Headers".
     Скопируй ВСЕ заголовки запроса (обязательно должен быть 'cookie:' / 'Cookie:').
       • Firefox: правой кнопкой по запросу → Copy → Copy Request Headers.
       • Chrome: раздел Request Headers → выдели всё → скопируй.
  6. Вставь их в файл  auth/headers_raw.txt  (создай, если нет) и сохрани.
  7. Запусти:  uv run auth_setup.py

После этого появится auth/browser.json и можно:  uv run fetch_tracks.py --liked
"""

from pathlib import Path

from ytmusicapi import setup

AUTH_DIR = Path(__file__).parent / "auth"
RAW = AUTH_DIR / "headers_raw.txt"
OUT = AUTH_DIR / "browser.json"


def main() -> None:
    AUTH_DIR.mkdir(exist_ok=True)
    if not RAW.exists() or not RAW.read_text(encoding="utf-8").strip():
        RAW.touch()
        raise SystemExit(
            f"Пустой файл заголовков: {RAW}\n"
            "Вставь туда Request Headers из браузера (инструкция вверху auth_setup.py) и запусти снова."
        )

    headers_raw = RAW.read_text(encoding="utf-8")
    if "cookie" not in headers_raw.lower():
        raise SystemExit(
            "В заголовках нет строки 'Cookie:'. Похоже, скопировался не тот блок.\n"
            "Нужны именно Request Headers запроса к /youtubei/v1/browse."
        )

    setup(filepath=str(OUT), headers_raw=headers_raw)
    print(f"✓ Авторизация сохранена в {OUT}")
    print("Теперь можно:  uv run fetch_tracks.py --liked")


if __name__ == "__main__":
    main()
