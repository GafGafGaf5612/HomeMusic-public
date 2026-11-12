# HomeMusic Pocket — весь HomeMusic на телефоне

Один APK, которому не нужны ни домашний ПК, ни VPS. На телефоне сразу работают Navidrome, Хаб и торрент-клиент.
Интерфейс — та же PWA Хаба, только внутри приложения. Рассчитано на Pixel 9 (Android 15/16, arm64); подходит любой arm64-телефон с Android 10+.

```
APK app.homemusic.pocket
├─ PocketService (foreground-сервис, уведомление-плеер, MediaSession)
│   ├─ Navidrome           — официальная статическая сборка linux/arm64, дочерний процесс, 127.0.0.1:4533
│   ├─ Хаб (FastAPI)       — Python 3.12 через Chaquopy, в процессе приложения, 127.0.0.1:8080
│   └─ торрент-клиент      — libtorrent (libtorrent4j) + Web API qBittorrent (QbitServer), 127.0.0.1:8081
├─ MainActivity            — WebView с PWA Хаба; navigator.mediaSession → экран блокировки и кнопки гарнитуры
└─ ffmpeg / ffprobe / qjs  — собраны из исходников под Android (пробы, ремукс yt-dlp, JS для YouTube)
```

## Что работает и что нет

| | На телефоне |
|---|---|
| Поиск и скачивание (hitmoz, hitmos, SoundCloud, YouTube, Яндекс Музыка), загрузка файлов, библиотека, плейлисты, «Понравившиеся», миксы | как на сервере |
| ИИ-ассистент | нужен ключ OpenRouter: «Настройки» (долгое нажатие на иконку или кнопка на экране запуска) |
| «Микс дня» | без аудио-векторов (эмбеддеру нужна видеокарта); с ключом Last.fm — с похожими треками |
| Раздачи | без Jackett (.NET и Linux-DNS на Android нет): поиск по rutor, Knaben и The Pirate Bay, только magnet-ссылки. Rutracker недоступен. Прослушивание до скачивания работает |
| YouTube | JS-движок — QuickJS (медленнее deno); без bgutil POT-провайдера часть роликов может не открыться |
| Несколько пользователей, приглашения, вход через Google | не нужны: один пользователь, вход автоматический |

Музыка хранится в `Music/HomeMusic` (нужно разрешение «Доступ ко всем файлам»; папка переживёт удаление приложения
и видна по USB) или в собственной папке приложения (без разрешения, удаляется вместе с ним). Выбор делается на
первом запуске. Navidrome доступен и другим плеерам на этом телефоне: `http://127.0.0.1:4533`, логин `admin`,
пароль копируется в «Настройках».

## Сборка

Нужны JDK 17+, Android SDK (platform 36, NDK r28), Rust с целью `aarch64-linux-android`, Python 3.12, Node 22+,
cmake, ninja, git (Go скачивается сам).

```bash
rustup target add aarch64-linux-android
NDK=$ANDROID_HOME/ndk/28.2.13676358 ./build.sh          # debug-APK
RELEASE=1 NDK=$ANDROID_HOME/ndk/28.2.13676358 ./build.sh # release (свой ключ — pocket.keystore в local.properties)
```

`build.sh` делает четыре вещи:

1. `native/build-wheels.sh` — колёса под Android, которых нет в индексе Chaquopy: pydantic-core и jiter (Rust/PyO3),
   selectolax (C), rapidfuzz (чистый Python);
2. `native/build-binaries.sh` — ffmpeg/ffprobe (только аудио) и QuickJS-ng, `native/build-navidrome.sh` — Navidrome
   из исходников под Android (`GOOS=android`, bionic): официальная сборка со статической glibc падает в приложении
   от seccomp (SIGSYS). Всё в виде `lib*.so`: Android запускает исполняемые файлы только из папки нативных библиотек;
3. PWA (`web/`, `npm run build`);
4. Gradle: Chaquopy ставит зависимости Хаба из `app/requirements.txt`, `tools/stage_hub.py` кладёт код Хаба
   с миграциями и PWA в `assets/hub.zip` (скомпилированным в .pyc).

Пути под конкретную машину — в `local.properties` (`sdk.dir`, `pocket.python`, `pocket.wheels`, `pocket.jniLibs`).

## Отладка

```bash
adb logcat -s Pocket PocketService Pocket-navidrome PocketTorrents PocketQbit python.stdout python.stderr
adb forward tcp:8080 tcp:8080   # Хаб телефона в браузере компьютера: http://localhost:8080
```

`hub-data/config.yaml` в данных приложения пишется один раз, ручные правки сохраняются.
