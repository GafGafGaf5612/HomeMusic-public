# Админ-аналитика: веса песен, карта рекомендаций, подробная статистика пользователей — ТЗ для исполнителя

> Документ для агента-исполнителя (Claude или ChatGPT / Codex). Составлен 2026-10-31 по коду `master` @ `e03239c`.
> Пути и строки могли сдвинуться — перед каждым этапом сверяйтесь с кодом. Всё, что помечено **(Р)**, — принятое
> решение, его не переспрашивать. Вопросы владельцу с ответами по умолчанию — §11.
>
> **Статус (2026-10-31): все 5 этапов сделаны и выкачены.** Этап 1 — сбор данных (§6.10), этап 2 — API админа (§7.10),
> этап 3 — проекция для карты (§8.3), этап 4 — экраны в PWA (§9.7), этап 5 — приложение 1.14.0 (§10, `PROGRESS.md`).

## 0. Как работать с этим документом

1. Прочитать: `CLAUDE.md` (git-поток, тесты, выкатка, правило QA), навык `writing-tests`, `MIXES.md` §3.2, §4.2, §5,
   §5.1, §6, §7, `hub/API.md` разделы «Admin», «Listening data», «Микс дня», «Моя волна».
2. Работа разбита на **5 этапов**. Каждый этап — отдельная задача по правилам `CLAUDE.md`: свой worktree и ветка,
   тесты, fast-forward слияние в `master`, push, выкатка. Порядок: **1 → 2 → 3 → 4**; этап 5 (Android, отдельный
   репозиторий) зависит только от контракта этапа 1 и может идти после него параллельно со 2–4.
3. **Этап 1 срочный**: пока он не выкачен, история «Моей волны» продолжает теряться (§3, п. 1). Его стоит влить
   первым и быстро, не дожидаясь экранов.
4. В ветке этапа 1 положить этот документ в репозиторий как **`INSIGHTS.md`** (рядом с `MIXES.md`), добавить его в
   список документов в `CLAUDE.md` (раздел «Что где», строка «Документы»; `CLAUDE.md` — общий источник и для Codex
   через `AGENTS.md`, отдельных копий не делать). Дальше документ двойной, как `MIXES.md`: по мере готовности
   разделы переписываются из ТЗ в «как устроено», в конце раздела — статус с датой.

## 1. Что хочет владелец

1. **Веса песен.** Видеть вес каждой песни у каждого пользователя ровно так, как его считает рекомендательная система
   (профиль вкуса v2), с разложением «почему такой вес» до отдельных событий. И сводную матрицу «песни × пользователи».
2. **Карта рекомендаций каждого пользователя** — наглядно:
   - «карта вкуса»: песни пользователя в звуковом пространстве (MuQ-MuLan), размер — вес, цвет — грань вкуса,
     отрицательные отдельно, сиды выделены; поверх — куда легли треки текущего микса и выбранной сессии волны;
   - «путь рекомендаций»: сигналы → профиль → сиды → генераторы → кандидаты → фильтры → 50 песен микса → что
     пользователь из этого запустил, дослушал, пропустил, лайкнул.
3. **Подробная аналитика по каждому пользователю** и сравнение пользователей: сколько прослушал (штук и времени),
   сколько раз запускал «Микс дня», сколько раз «Мою волну» — **с какой песни / исполнителя или своя (с главной,
   из каталога), из какого места интерфейса**, пропуски, ♥ и «Не нравится», новые песни и исполнители, источники
   прослушивания, устройства и версии приложения, время суток, какие генераторы рекомендаций работают, какие
   рекомендации «прижились» и т. п.

## 2. Что уже есть (инвентарь — чтобы не искать заново)

| Что | Где | Важное |
|---|---|---|
| События прослушивания | `play_events` (`hub/app/models.py` `PlayEvent`), запись — `hub/app/plays.py` `record_events`, API `POST /api/plays/events` | `kind` start/progress/interrupted/error/skip/finish/like/unlike/dislike/undislike; `context` (`mix:<id>`, `wave:<sid>`, `smart:<sid>` (рекомендация умного перемешивания, MIXES.md 8), `album`, `playlist`, `search`, `artist`, `liked`, `queue`, `continuous`, `other`, `favorite:bootstrap\|reconcile`, `NULL`); `client` (`app/<ver>`, `pwa`, `navidrome`, `assistant…`); `playback_id`+`device_id`+`listened_ms` (checkpoints); `track_metadata` (Хаб дополняет при записи из библиотеки и `catalog_cache`); `superseded_scrobble` |
| Итоги прослушивания | `hub/app/listening_summary.py` `summary()`, `GET /api/plays/summary` | Правило засчитанного прослушивания: измеренные ≥ 30 с (или половина трека короче минуты) + scrobbles Navidrome; старые `finish` без checkpoints — «legacy», не засчитываются |
| Журнал ♥ | `hub/app/favorites.py` (`fold`, `snapshot`, `State`), `favorite_subjects` | Типизированный ♥ track/album/artist, компенсации; контексты `favorite:bootstrap/reconcile` — не действия пользователя; ♥ импорта не свои (M4) |
| «Не нравится» | `dislikes`, `plays.add_dislike` | Трек или исполнитель, `source` app/pwa/yandex_import |
| Профиль вкуса v2 (рабочий, `config.yaml` `mixes.taste_model: v2`) | `hub/app/mixes/taste_snapshot.py` `load_snapshot` (локально, без сети и записи) + `hub/app/mixes/taste_math.py` `calculate_profile` (чистая функция) | `V2Profile.tracks[key] → TasteTrack` (`positive_raw` P, `negative` N, `score` s, `liked`, `downloaded`, `album_liked`, `heard30`, `last_heard`, `last_positive`, `positive_age`, `familiar`); `artists` (A_artist), `artist_listens`, `evidence`, `maturity`, `clock` (активные дни), `explicit_negative_keys`, `favorite_states` |
| Локальный v1 | `taste_math.calculate_baseline` | Для сравнения; рабочий онлайн-v1 (`taste.build_profile(model="v1")`) ходит в сеть — для админки **не использовать** |
| Грани и сиды | `hub/app/mixes/taste_select.py`: `prepare` (embedder `/v2/profile`, модель не грузит), `facet_names`, `select_profile_seeds` (чистая, детерминирована по пользователю и местной дате) | `seeds()` зовёт `build.cold_seeds` → сеть Deezer: в админке **не вызывать** |
| Сборка микса | `hub/app/mixes/build.py`: `keep_reason`, `score`, `select_songs`, `order`, `build` | `Mix.stats_json`: `mode`, `taste_model`, `seeds`, `seed_keys`, `generators{tracks, used, seconds, error?, off?}`, `candidates`, `kept`, `familiar`, `facets` (с `members`), `maturity`, `embedder`, `build_seconds`; `MixItem`: `position`, `state` active/reserve/failed, `familiar`, `generator`, `score`, `features_json{track, generators, sim, rank, familiar, cleanup?}` |
| Волна | `hub/app/mixes/wave.py`, `wave_sessions` | `seed_json` (`{ref, artist, title}` или `{artist_ref\|nd_artist_id, artist}`; нет — своя волна), `state_json` (pool, out, `played` ≤ 50 без генератора, `gens`, `skips`, `started`…). **Строки старше 7 дней без запросов удаляются** в `wave.start()` (`delete(WaveSession)…KEEP`) |
| embedder v2 | `embedder/app/main.py`, `geometry.py`; клиент Хаба `hub/app/mixes/embedder.py` | `/v2/profile` (грани: `id, weight, members, center`), `/v2/similarity`, `/v2/vectors` (центрированные векторы, 512 чисел); ключи `nd:<song>` / `dz:…`; 409 `stale_space`. **Проекции в 2D нет**, в Хабе нет numpy |
| Админка | `hub/app/api/admin.py` (`/api/admin/users`, `/users/{id}`, приглашения), PWA `web/src/pages/Admin.tsx`, `AdminUser.tsx` (`Stat`, `UserBadges`), маршруты в `web/src/App.tsx` | `admin_user` в `hub/app/auth.py` → 403 не-админу. Мок — `web/mock/plugin.ts` (раздел `// --- admin`) |
| PWA | React 19 + Tailwind 4, **никаких библиотек графиков** (в `dependencies` только react, react-dom, шрифт) | Токены цветов в `web/src/index.css` (тёмная тема, светлая `hm-light`, `--hm-bg` во встроенном режиме); `Sheet`, `Segmented`, `Notice`, `SectionTitle` в `components/ui.tsx` |
| Запуск волны в PWA | `web/src/lib/wave.ts` `startWave(seed)`; вызовы: `components/Wave.tsx` (карточка на `/home` и в Каталоге), `components/TrackMenu.tsx` (по треку / исполнителю), `pages/Player.tsx`, `pages/Artist.tsx`, `pages/MediaLibrary.tsx` (по исполнителю библиотеки) | Тело запроса — только `{seed, mode}` |
| Запуск волны в приложении | `android/…/homemusic/HubWave.kt` `start(activity, seed, fallback)`: карточка «Моя волна» на «Главной», меню трека (`bindRow`), «Радиостанции» (`startArtist`), `startArtistExact`; пересоздание сессии после 404 (`WaveSession.startBody`) | Источник не передаётся |
| Контексты приложения | `HubPlays.kt`, `NativeContexts.kt`, `MediaManager.startQueue(…, context)` | ▶ «Микса дня» на «Главной» — `mix:<id>`; плейлисты Navidrome (в т. ч. плейлист «Микс дня») — просто `playlist` **без id** |

Объём рабочих данных на 2026-10-31 (агрегаты): 3 пользователя, ~1,8 тыс. событий, 15 миксов, ~1 тыс. `mix_items`,
21 строка `wave_sessions` при 26 разных `wave:<sid>` в событиях. Вычисления «на лету» по запросу админа укладываются
с запасом; проектировать с расчётом на рост (индексы, ограничения выдачи), но без преждевременных кэшей.

## 3. Чего не хватает и почему это важно

1. **История волны теряется.** Строка `wave_sessions` (единственное место, где записано, с чего запущена волна)
   удаляется через 7 дней. Уже 5 из 26 сессий остались только в событиях — без сида. Источник в интерфейсе (главная,
   меню трека, плеер, «Радиостанции»…) не записывается вообще, клиент тоже.
2. **Что волна предлагала** (генератор, оценка, знакомое/новое) не хранится: в `state_json.played` ≤ 50 сыгранных и
   без генератора, остальное — пул в памяти. Нельзя посчитать, какие генераторы волны работают.
3. **Запуски списков** не размечены: у событий есть контекст, но нет id «запуска». Сколько раз запускали микс,
   можно только оценить эвристикой по времени (§7.5). Плейлист «Микс дня» в приложении пишется как `playlist` без id —
   такие прослушивания нельзя честно отнести к миксу.
4. **Микс**: не хранится разложение оценки песни (вклад генераторов / звука / популярности / штрафов) и сколько
   кандидатов отсеяно по каждой причине (`disliked`, `version`, `duration`, `recent`, `negative_score`).
5. **Вес песни нельзя объяснить**: профиль хранит только итоговые P, N, s — без вклада отдельных событий.
6. **Нет 2D-координат** для карты: векторы 512-мерные, лежат в embedder.
7. **Имена** треков, известных только по `ref`, без сети частично неизвестны (профиль v2 их не дозаполняет).

## 4. Принципы (Р)

- **Только чтение.** Админские GET не пишут в БД и не меняют состояние: никаких `reconcile`, сверки с Navidrome,
  постановки загрузок, заданий в embedder (`/embed`), загрузки модели (`/text`), обращений к Deezer/YTM/Last.fm/Яндексу.
  Исключение — уже существующие запросы `admin.py` к Navidrome за числом ♥/плейлистов.
- **Та же математика, что в работе.** Веса считаются тем же кодом (`load_snapshot` + `calculate_profile`), что и
  для миксов/волны, с моделью из `cfg.mixes.taste_model`; v1 — только как локальный baseline с пометкой. Никакой
  второй реализации формул в Хабе или в PWA. PWA только показывает.
- **Сбор данных совместим со старыми клиентами.** Новые поля необязательны; старое приложение и старый Хаб работают
  как раньше. Для Android — capability в `/healthz` (как `typed_favorites_v1`).
- **Новые поля событий не влияют на вкус.** `launch_id`, `launch_origin`, `origin` волны — только для аналитики:
  профиль, сиды, оценки миксов и волны от них не меняются (тест на это обязателен).
- **Приватность.** Всё новое — только `is_admin` (`Depends(admin_user)`, иначе 403); ответы с `Cache-Control: no-store`;
  персональные данные (названия треков, события) не писать в логи; скриншоты с реальными данными в git не коммитить
  (только папки из `.gitignore`, `CLAUDE.md` п. 2); в мок и тесты — только синтетика.
- **Проверки на рабочем Хабе** — по правилу QA из `CLAUDE.md`: под владельцем не входить. Админские экраны требуют
  админа, поэтому проверка — мок, тесты, изолированный прогон (§9.5); на рабочем Хабе смотрит владелец.
- **PWA без новых npm-зависимостей**: графики — свой SVG (§9.2). Ширина 375 px без горизонтальной прокрутки страницы;
  широкие таблицы — во внутреннем контейнере с прокруткой и закреплённым первым столбцом.
- **История не удаляется и не переписывается** (решение владельца по профилю v2). Миграции только расширяющие.

## 5. Словарь метрик (одинаковый для API, экранов и документа)

| Термин | Определение |
|---|---|
| Попытка | Одна попытка проиграть трек: события с одним `(device_id, playback_id)`; у старых событий без них — один `start` и следующий за ним `skip`/`finish` того же трека на том же клиенте |
| **Прослушивание (засчитанное)** | Как в `listening_summary`: попытка с измеренным `listened_ms` ≥ порога (`listened_threshold_ms`: 30 с или половина трека короче минуты) **или** scrobble Navidrome (не `superseded_scrobble`). Это главное число «сколько прослушал» |
| Прослушивание по событиям | `finish` или `skip` с `position_s ≥ 30` (как `heard30` профиля). Показывать рядом для старой истории, где нет checkpoints; подпись «по событиям» |
| Время прослушивания | Сумма прироста `listened_ms` по попыткам (как в `summary`); у старых событий неизвестно — не выдумывать |
| Дослушал | `finish` (до конца или ушёл после ≥ 90 %) |
| Ранний пропуск | `skip` с `position_s < 30` (вес M4 −1) |
| Поздний пропуск | `skip` с `position_s ≥ 30` (вес 0…+2, `taste.skip_weight`) |
| **Запуск** | Пользователь начал играть список: микс, волну, альбом, плейлист и т. д. Точный — по `launch_id` (после этапов 1/5) или по сессии волны; для старых событий — эвристика §7.5 с пометкой `inferred` |
| Источник (вид запуска) | По контексту: `mix`, `wave`, `smart` (рекомендации умного перемешивания; свои песни списка остаются в `playlist`/`liked`), `playlist`, `album`, `artist`, `liked`, `search`, `queue`, `continuous` («Tempus продолжил похожими»), `other`, `unknown` (контекста нет) |
| Место запуска волны (`origin`) | Где в интерфейсе нажали (§6.3). Нет у старых клиентов → `null`, на экране «не записано» |
| Сид волны (`seed_kind`) | `own` (своя волна), `track`, `track_artist` (волна по исполнителю трека), `artist` (исполнитель каталога), `library_artist` («Радиостанции», `nd_artist_id`), `file` (файл из поиска) |
| Перезапуск волны | Приложение само создало новую сессию после 404 (`origin=restart`) — не действие пользователя, в «запуски» не входит, показывается отдельно |
| Открытие | Трек (исполнитель), впервые за всю историю пользователя прослушанный в выбранном периоде |
| Прижившаяся рекомендация | Трек, впервые прослушанный из микса/волны, который потом получил собственный ♥ или ≥ 2 прослушивания в самостоятельно выбранных контекстах (`album`, `playlist`, `search`, `artist`, `liked`, `library`) |
| Активный день | Местный день (`Settings.timezone`, по умолчанию Europe/Moscow) с ≥ 1 прослушиванием |
| Вес песни `s` | v2: `s = sat(P) − N` (MIXES.md §5.1). `P` — сумма положительных вкладов M4 (finish +2, поздний skip 0…+2, собственный ♥ трека +1) × `w`, `N` — сумма модулей отрицательных (ранний skip −1, «Не нравится» −5) × `w`; `w = 0.5^(A/30)`, `A` — активные единицы после дня события; вклад с `w < 0.01` или старше 3 лет — 0; `sat(x) = x` при `x ≤ 2`, иначе `2 + 2·ln(1 + (x−2)/2)` |
| Вес исполнителя `A_artist` | `sat(Σ max(s,0)) − 0.5·sat(Σ max(−s,0)) + ♥исполнителя·w + 0.5·Σ ♥альбома·w` |
| Зрелость | `E = Σ max(s, 0)`, `m = min(1, E/20)` (уменьшает звуковое слагаемое и задаёт долю онбординга в сидах) |

Время везде хранится в UTC, дни и часы на экранах — местные (`Settings.timezone`). События с `ts > now`, клиенты
`qa` и `*-debug` и импортные ♥ исключаются так же, как в `taste_math.visible_events`.

---

## 6. Этап 1 — сбор недостающих данных (Хаб + PWA) — сделан 2026-10-31 (§6.10)

Ветка, например, `feat/insights-data`. Затрагивает `hub/` и `web/` (одна задача: «начать записывать то, что нужно
аналитике»). Экранов админки здесь нет.

### 6.1. Миграция (одна, расширяющая)

Новая ревизия Alembic поверх текущей головы (проверить `alembic heads`; на 2026-10-31 — `6a3d8c2e710f`):

- `wave_sessions` + колонки: `origin` String(32) NULL, `client` String(32) NULL, `restart_of` String(32) NULL,
  `taste_model` String(8) NULL, `request_json` JSON NULL (тело `POST /api/wave/start` как пришло: seed, mode, origin…),
  `next_calls` Integer NOT NULL default 0, `cleared_at` DateTime NULL. Индекс `(user_id, created_at)`.
- Новая таблица **`wave_items`** — что волна выдала:
  `id` PK, `session_id` String(32) FK `wave_sessions.id` ON DELETE CASCADE (индекс), `user_id` FK `users.id`
  ON DELETE CASCADE, `first_handed_at`, `last_handed_at` DateTime, `handed_count` Integer, `position` Integer (порядок
  первой выдачи в сессии), `ref` String(64), `nd_song_id` String(64) NULL, `key` String(64) (ключ вектора),
  `artist`, `title` String(256), `generator` String(32), `gens` JSON (генератор → позиция), `familiar` Boolean,
  `score` Float, `sim` Float NULL, `value` Float NULL (итоговое значение отбора волны). Уникальность `(session_id, ref)`.
- `play_events` + `launch_id` String(64) NULL, `launch_origin` String(32) NULL; индекс `(user_id, launch_id)`.
- Данные не переносить и не дописывать задним числом. Downgrade — удаление добавленного.

### 6.2. История волны больше не удаляется

- В `wave.start()` заменить `delete(WaveSession)…` на «очистку»: для строк с `updated_at < now − KEEP` и
  `cleared_at IS NULL` заменить `state_json` компактной выжимкой
  `{"cleared": true, "version": …, "taste_model": …, "gens": …, "started": …, "started_familiar": …, "skips": …,
  "seeds_used": …, "downloads": …}` (пул/out/played — тяжёлые, они не нужны) и поставить `cleared_at`.
  Сессия старше `IDLE` (24 ч) всё равно отвечает 404 — проверить, что `_runtime()` не пытается её продолжить и что
  `State.load` переживает выжимку.
- `start()` записывает `origin`, `client`, `restart_of`, `taste_model` (= `cfg.mixes.taste_model` на момент старта),
  `request_json`. `next_batch()` увеличивает `next_calls`.
- Обновить `MIXES.md` §7.1 («строки старше 7 дней удаляются» → «очищаются до выжимки, история сессий хранится»).

### 6.3. Место запуска волны

`WaveStartIn` (`hub/app/api/schemas.py`) + необязательные поля:

```python
origin: str | None = Field(default=None, max_length=32, pattern=r"^[a-z][a-z0-9_]*$")
client: str | None = Field(default=None, min_length=1, max_length=32)   # "pwa", "pwa-app", "app/<versionName>"
restart_of: str | None = Field(default=None, max_length=32)              # id сессии, которую пересоздаёт приложение
```

Словарь `origin` (неизвестное значение сохраняется как есть — новые клиенты могут добавлять места; в API.md — таблица):

| `origin` | Где нажали |
|---|---|
| `home_card` | карточка «Моя волна» на «Главной»: нативная в приложении, `/home` в браузере |
| `catalog_card` | карточка «Моя волна» в «Каталоге» PWA (`/search` без запроса; в приложении — вкладка «Хаб») |
| `track_menu` | «Волна по треку» в меню трека (списки, альбом, микс, поиск) |
| `artist_menu` | «Волна по исполнителю» в меню трека |
| `player` | из плеера: кнопка «Моя волна по треку» на `/player`, меню трека в мини-плеере и очереди, меню в плеере приложения |
| `artist_page` | кнопка «Волна по исполнителю» на странице исполнителя |
| `radio` | «Радиостанции» приложения, список исполнителей библиотеки PWA (`nd_artist_id`) |
| `search_file` | меню «⋯» результата поиска файлов (`file:<provider>:<id>`) |
| `restart` | приложение само пересоздало сессию после 404 (вместе с `restart_of`) |

`seed_kind` не хранится, а выводится (§5): из `request_json.seed` (с `by_artist`), для старых строк — из `seed_json`
(`ref` → `track`, `ref` начинается с `file:` → `file`, `artist_ref` → `artist`, `nd_artist_id` → `library_artist`,
нет сида → `own`).

### 6.4. Что волна выдала — `wave_items`

В `_hand_out()` для каждого выданного трека — upsert в `wave_items` по `(session_id, ref)`: при первой выдаче — все
поля и `position` (следующий номер в сессии), при повторной (трек вернулся в пул и выдан снова) — `handed_count += 1`,
`last_handed_at`, свежие `score/sim/value`. `generator` — `wave.top_generator(c)`, `value` — значение из `pick()`
(вынести вычисление так, чтобы его можно было сохранить, не меняя отбор). Запись — в той же сессии БД, что
`_save()`; ошибка записи аналитики **не должна** ломать выдачу волны (лог + продолжить), тест на это.

### 6.5. Запуски списков — `launch_id`, `launch_origin`

`PlayEventIn` + необязательные `launch_id` (1–64 символа) и `launch_origin` (как `origin`, ≤ 32, тот же pattern);
`record_events` сохраняет их в новые колонки. Смысл: все события одного запуска списка несут один `launch_id`
(новый при каждом новом списке/очереди), `launch_origin` — где запустили. Для волны `launch_id = "wave:<sid>"`.
Словарь `launch_origin` для миксов и списков:

| `launch_origin` | Где |
|---|---|
| `mix_page` | «Слушать всё» на странице микса |
| `mix_page_track` | тап по треку на странице микса |
| `mix_card` | ▶ на карточке «Микс дня» («Главная» приложения, «Каталог»/«Главная» PWA) |
| `nd_playlist` | плейлист Navidrome в приложении (в т. ч. «Микс дня») |
| `album`, `artist`, `playlist`, `liked`, `search`, `queue`, `library` | тап/«Слушать всё» на соответствующем экране |
| `wave` | очередь волны |

- В `/healthz` (`HealthOut.capabilities`) добавить `launch_events_v1` и `wave_origin_v1` — по ним приложение решает,
  слать ли новые поля (этап 5). Старый Хаб лишние поля молча игнорирует (pydantic по умолчанию), так что вреда нет,
  но gating — принятый в проекте способ.
- `/api/wave/{id}/next` принимает `played` тем же валидатором — поля проходят и там.
- Профиль вкуса эти поля не читает: тест «тот же набор событий с `launch_*` и без — одинаковый `V2Profile`».

### 6.6. Микс: разложение оценки и причины отсева

- В `build.py` вынести `score_parts(c, weights, profile) -> dict` c ключами `generators`, `sound`
  (`1.5·m·sim` для v2, `1.5·sim` для v1), `popularity`, `track_penalty`, `artist_penalty`, `maturity`, `total`;
  `score()` возвращает `score_parts(...)["total"]` — **числа не меняются** (существующие `test_score`,
  `test_mix_build` должны остаться зелёными без правок ожиданий; добавить тест «сумма частей == score»).
- `Cand.features()` дополнительно пишет `score_parts` и `profile` — снимок известного профилю трека на момент сборки:
  `{key, s, positive, negative, artist_score, familiar}` (или `null`).
- В `build()` считать `stats["rejected"] = {reason: count}` по `keep_reason` (все кандидаты, не только первые 500).
- То же для shadow (`mixes/shadow.py`) не трогать.

### 6.7. PWA — отправлять новые данные

- `lib/wave.ts`: `startWave(seed, origin)`; тело `{seed, origin, client}`; `client` — `'pwa'` в браузере,
  `'pwa-app'` при `EMBEDDED`. Проставить `origin` во всех местах вызова (§2 «Запуск волны в PWA»): `Wave.tsx` —
  по маршруту (`/home` → `home_card`, иначе `catalog_card`); `TrackMenu.tsx` — `track_menu`/`artist_menu`, а если
  меню открыто в мини-плеере/очереди/`/player` — `player` (передать признак места в меню пропсом); `Player.tsx` —
  `player`; `Artist.tsx` — `artist_page`; `MediaLibrary.tsx` — `radio`; меню результата поиска файлов — `search_file`.
- `lib/listen.ts` / `lib/plays.ts`: каждый новый список (`listenAll`, запуск очереди) получает `launchId`
  (`newEventId()`), все события его треков несут `launch_id` и `launch_origin`; восстановление после перезагрузки
  (`restoreListening`) сохраняет тот же `launchId`. Волна — `launch_id = wave:<sid>`.
- `lib/player.ts` (мост в приложение): каждому элементу `playQueue` добавить `launch_id` и `launch_origin`
  (старое приложение их не читает; новое — этап 5).
- `pages/Mix.tsx`: `mix_page` / `mix_page_track`; карточка микса — `mix_card`, если она запускает воспроизведение.
- Типы — `web/src/api/types.ts`; мок `web/mock/plugin.ts` принимает новые поля без ошибок.

### 6.8. Тесты этапа 1 (Хаб, pytest; правила — навык `writing-tests`)

- Миграция применяется на копии схемы (общий фикстурный `migrated_db`), старые строки `wave_sessions` читаются.
- Очистка: сессия старше 7 дней не удаляется, `state_json` → выжимка, `cleared_at` стоит; свежая не тронута;
  продолжение очищенной сессии → 404 как раньше.
- `POST /api/wave/start` с `origin/client/restart_of` сохраняет их и `request_json`; без них — `NULL`; плохой
  `origin` (пробел, заглавные, > 32) → 422.
- `wave_items`: первая выдача, повторная (`handed_count`), отказ записи аналитики не ломает ответ волны.
- `next_calls` растёт.
- `launch_id/launch_origin` сохраняются из `/api/plays/events` и из `played` в `/next`; профиль v2 и оценка микса
  с ними и без них совпадают.
- `score_parts` в сумме даёт прежний `score` для v1 и v2 (отрицательный `s`, отрицательный `A_artist`, нет `sim`);
  `stats.rejected` считает причины.
- PWA: `npm --prefix web run build`.

### 6.9. Документы, выкатка, приёмка

- `hub/API.md`: новые поля `WaveStartIn`, `PlayEventIn`, capabilities, словари `origin`/`launch_origin`.
  `MIXES.md` §3.2 (поля событий), §6.2 (`score_parts`, `profile`, `stats.rejected`), §7.1 (история сессий,
  `wave_items`). `INSIGHTS.md` (этот документ) + строка в `CLAUDE.md`. `PROGRESS.md` — запись этапа.
- Выкатка Хаба по `CLAUDE.md` п. 7 (проверка активных загрузок, `docker compose up -d --build hub`, `healthy`,
  `/healthz` с новым `web_build` и capabilities, `docker image prune -f`).
- **Готово, когда:** новые сессии волны из PWA пишутся с `origin` и `client`; строки старше 7 дней остаются;
  `wave_items` наполняется; события PWA несут `launch_id`; новые миксы содержат `score_parts` и `stats.rejected`.

### 6.10. Как устроено (этап 1 сделан 2026-10-31)

- Миграция `c5f1a8d3e2b7_insights_data` поверх `6a3d8c2e710f`: колонки `wave_sessions` (+ индекс
  `ix_wave_sessions_user_created`), таблица `wave_items` (`uq_wave_items_session_ref`), `play_events.launch_id` /
  `launch_origin` (+ `ix_play_events_user_launch`). Только расширение, старые строки не тронуты; downgrade удаляет добавленное.
- `hub/app/mixes/wave.py`: `clear_old()` вместо удаления (выжимка — `CLEARED_KEYS` + `"cleared": true`, `cleared_at`;
  очищенная сессия убирается и из памяти, `_rt`, поэтому продолжение — 404 и без рестарта Хаба); `start(..., origin,
  client, restart_of, request)`; `next_calls` растёт в транзакции записи событий `next`; `save_items()` — upsert
  `wave_items` после `_save()`. **Отличие от §6.4:** запись идёт отдельной транзакцией сразу после состояния сессии,
  а не в той же: так ошибка аналитики не откатывает состояние волны (лог `wave …: handed out tracks not recorded`).
  `value` — из `pick(..., values=)`: значения считаются один раз и те же идут в сортировку, отбор не изменился; у сида
  (`first`) `value` — `null`.
- `hub/app/mixes/build.py`: `score_parts()`, `score()` = `score_parts()["total"]` (порядок сложения прежний, числа
  бит-в-бит), `known_of()` — снимок профиля; `Cand.parts` / `Cand.known` ставит `build()` для активных и запасных песен,
  `features()` пишет `score_parts` и `profile`; `stats.rejected` — все пять причин, нули тоже.
- API: `WaveStartIn.origin/client/restart_of`, `PlayEventIn.launch_id/launch_origin` (`ORIGIN_PATTERN` в
  `api/schemas.py`), capabilities `launch_events_v1`, `wave_origin_v1`; `record_events` сохраняет поля, `wave.record`
  передаёт их из `played` как есть (Хаб сам `launch_id` не придумывает).
- PWA: `startWave(seed, origin)` (`client` — `pwa` / `pwa-app`); `TrackMenuButton inPlayer` в мини-плеере и на
  `/player` → `origin=player`; меню файла поиска → `search_file`. `plays.newLaunch` / `launchOrigin`; плеер страницы
  (`listen.ts`) держит `listLaunch` списка, он в каждом событии попытки, в checkpoint и в сохранённой сессии плеера
  (перезагрузка продолжает тот же запуск); мост `playQueue` — новый запуск на каждый вызов. Карточка «Микс дня» в PWA —
  ссылка, воспроизведение не запускает, поэтому `mix_card` в PWA не встречается (только приложение, этап 5).
- Тесты: `test_wave.py` (origin/client/request_json, 422 на плохой origin, очистка вместо удаления и 404, `wave_items`
  с повторной выдачей и `next_calls`, отказ записи аналитики, `launch_*` из `played`), `test_plays.py` (сохранение
  `launch_*`, отказ плохих, профиль v2 и оценка с полями и без), `test_taste_consumers.py` (`score_parts` = `score` для
  v1/v2, отрицательные `s` и `A_artist`, без `sim`; снимок профиля), `test_mix_build.py` (`stats.rejected`, `features`),
  `test_migration_insights.py` (upgrade со старыми строками, downgrade).

---

## 7. Этап 2 — расчёты и API админа (Хаб) — сделан 2026-10-31 (§7.10)

Ветка, например, `feat/insights-api`. Только `hub/` (+ документы). Всё — только чтение.

### 7.1. Где код

- Пакет `hub/app/insights/`: `names.py` (имена без сети), `taste_view.py` (профиль, разложение, матрица),
  `launches.py` (запуски), `analytics.py` (статистика периода), `mixes_view.py`, `waves_view.py`
  (`maps.py` — этап 3).
- Роутер `hub/app/api/insights.py` с `prefix="/api/admin"`, у каждого эндпоинта `Depends(admin_user)`;
  подключить в `hub/app/main.py` так же, как остальные. Схемы ответов — в `api/schemas.py` отдельным разделом
  `# --- admin insights` (если раздел вырастет слишком большим — отдельный модуль, импортируемый оттуда).
- Хаб отдаёт **коды** (`origin`, `seed_kind`, `source`, `generator`, причины отсева) и, где они уже есть в коде,
  готовые русские названия (`GENERATOR_TITLES`, `wave.generator_title`, `MODE_TITLES`); остальные подписи — в PWA
  (`lib/format.ts`, как `downloadStatusLabel`).

### 7.2. Профиль для админа — без побочных эффектов

```python
async def admin_profile(s, user_id, *, model: Literal["v1", "v2"] | None, now) -> V2Profile | Profile:
    snapshot = await load_snapshot(s, user_id, now=now)          # локально, пакетами, без сети
    tz = get_settings().timezone
    model = model or get_settings().cfg.mixes.taste_model
    if model == "v2":
        return calculate_profile(snapshot, now=now, timezone=tz, trace=...)   # trace — §7.3
    return calculate_baseline(snapshot, now=now, timezone=tz, mode="occurred-at")  # пометка "локальный v1"
```

- **Не вызывать**: `taste.build_profile(model="v1")` (сеть Deezer в `_names/_catalog_info`, сверка при `starred`),
  `taste_select.seeds()` и `build.cold_seeds` (сеть), ничего из `wave._rt`.
- Грани: `taste_select.prepare(p)` под `asyncio.wait_for(…, 8)`; `EmbedderDown`/таймаут → грани `null` и причина
  в ответе, остальное работает.
- Сиды «на сегодня»: `select_profile_seeds(p, cfg.mixes.seeds)` (чистая) + число мест под онбординг
  `round((1 − m)·8)` и выбранные исполнители онбординга. Подпись: «без холодного старта и сетевого обогащения;
  что реально взял микс — в карточке микса» (там `stats_json.seeds` — истина).
- Пользователь-QA: если в `config.yaml` задан `analytics.exclude_users` (список имён, по умолчанию пусто), такие
  пользователи по умолчанию не входят в общие итоги и матрицу (переключатель `include_excluded=true`).

### 7.3. Разложение веса — `trace` в `taste_math`

- В `calculate_profile(..., trace: Trace | None = None)` и `_calculate` добавить необязательный сборщик. При
  `trace=None` код и результат **не меняются** (тест: профиль с `Trace()` и без — равны по всем полям; в
  `calculate_offline`/P3 trace не передаётся).
- Записывать вклад в каждой точке, где меняется `positive_raw`/`negative`, и пропущенные события с причиной:

```python
@dataclass(frozen=True)
class Contribution:
    kind: str                 # finish | skip | start | like | dislike | dislike_legacy | download | album_like
    ts: datetime
    event_id: str | None
    side: Literal["P", "N", "0"]
    base: float               # вес M4 до затухания: +2, −1, skip_weight(...), +1, −5, 0
    age: float | None         # A — активные единицы после дня события
    w: float                  # множитель затухания (0, если w < MIN_WEIGHT или старше 3 лет)
    value: float              # base × w (в P или в N)
    context: str | None = None
    client: str | None = None
    position_s: float | None = None
    duration_s: float | None = None
    note: str | None = None   # "старше 3 лет", "w < 0.01", "start — вес 0", "♥ снят — вклад 0", "импорт — не свой"

@dataclass
class Trace:
    tracks: dict[str, list[Contribution]] = field(default_factory=dict)   # по итоговому TasteTrack.key
```

- Итог по треку в API: `P` (сумма), `sat(P)`, `N`, `s`, счётчики (`starts`, `finishes`, `early_skips`, `late_skips`,
  `heard30`), флаги, вклад в `A_artist`.

### 7.4. Имена треков без сети — `insights/names.py`

Пакетно, по парам `(ref, nd_song_id)`, в порядке доверия: таблица `Track` (по `nd_song_id`, по `deezer_track_id`) →
`TasteTrack.artist/title` → последнее `play_events.track_metadata` (`title`, `artists[].name` / `artist_credit`) →
`catalog_cache` `mix:track:<ref>` и `dz:track:<id>` → `mix_items.features_json.track` → `wave_items` →
«Неизвестный трек» + `ref`. Ни одного сетевого запроса. Флаг `owned` — есть ли песня в библиотеке (`nd_song_id`).

### 7.5. Запуски — `insights/launches.py`

Вход: события пользователя `kind ∈ {start, progress, interrupted, error, skip, finish}`, не
`superseded_scrobble`, `client != "navidrome"`, `ts ≤ now`, в порядке `(ts, id)`.

1. **С `launch_id`** — запуск = все события с этим `launch_id`; `origin` = `launch_origin` первого события.
2. **Волна** — запуск = сессия: строка `wave_sessions` (+ выжимка) или, если строки нет, «восстановленная» по
   событиям с `context = wave:<sid>` (`reconstructed: true`, сид и место неизвестны). Перезапуски (`origin=restart`)
   — отдельный счётчик, не «запуски пользователем».
3. **Остальное без `launch_id` (старая история)** — эвристика, у запуска `attribution: "inferred"`:
   поток = `device_id`, иначе `client`; новый запуск, когда на потоке приходит `start` с другим нормализованным
   контекстом, чем у текущего запуска, или когда с последнего события потока прошло > 30 мин (`LAUNCH_GAP`).
   Прочие события попытки относятся к запуску своего `start` (по `playback_id`, иначе к текущему на потоке).
   Контексты `favorite:*` — не воспроизведение, пропускаются; `NULL` → `unknown`.
4. **Привязка к миксу:** `mix:<id>` → этот микс; `playlist:<id>` (после этапа 5), если id — `nd_playlist_id`
   миксов пользователя → микс, действовавший в момент запуска (`created_at ≤ ts < closed_at` либо `active`);
   старый `playlist` без id → микс, если ≥ 60 % начатых в запуске треков — активные песни микса, действовавшего в тот
   момент (`attribution: "inferred_playlist"`).

Метрики запуска: `started_at`, `ended_at` (последнее событие), `source`, `context`, `mix_id`/`wave_id`, `origin`,
`client`, `device`, `tracks_started` (уникальные), `heard` (по правилу §5), `finished`, `early_skips`,
`late_skips`, `listened_ms`, `likes`, `dislikes` (события like/dislike с тем же контекстом между `started_at` и
`ended_at` + 5 мин), `first_track`.

### 7.6. Эндпоинты (все `GET`, только админ, `no-store`)

| Путь | Что |
|---|---|
| `/api/admin/insights/overview?from=&to=` | Все пользователи: строка на пользователя (прослушивания, время, активные дни, запуски микса, запуски волны (+ перезапуски), ♥, 👎, открытия, последняя активность, версия приложения) + общие итоги |
| `/api/admin/users/{id}/listening?from=&to=` | Статистика периода (ниже) |
| `/api/admin/users/{id}/taste?model=` | Сводка профиля: модель, `now`, timezone, `evidence`, `maturity`, сумма активных единиц, счётчики треков (s>0, s≥1, s<0, ♥, загрузки, альбомные, явные 👎), грани (`id, weight, artists[2], size, top_members[5]` с именами), сиды на сегодня, резерв онбординга, топ-50 исполнителей (`A_artist`, прослушивания, ♥ исполнителя, 👎), `limitations`, состояние embedder |
| `/api/admin/users/{id}/taste/tracks?model=&filter=&q=&sort=&limit=&offset=` | Треки профиля постранично. `filter`: `all\|positive\|seed\|negative\|liked\|disliked\|downloaded`; `sort`: `s\|p\|n\|last_heard\|plays\|title` (по умолчанию `s` по убыванию); строка: `key, ref, nd_song_id, artist, title, owned, s, p, sat_p, n, liked, disliked, downloaded, album_liked, heard30, familiar, seed_eligible, last_heard, plays, early_skips, facet_id` |
| `/api/admin/users/{id}/taste/track?key=&model=` | Разложение одного трека: всё из строки + `contributions[]` (§7.3, новые сверху), формула с подставленными числами, блок исполнителя, отдых (когда снова может попасть в «Любимое»/«Тайник», исходя из 7/60 дней и `last_heard`), косинусы к граням (если embedder доступен; `/v2/similarity` по одному ключу недостаточно — взять из этапа 3, до него `null`), где трек был в миксах (`mix_id`, дата, `state`, `position`, `generator`, `score`, итог) и в волнах (`wave_items` + итог) |
| `/api/admin/taste/matrix?by=track\|artist&filter=&q=&sort=&limit=&offset=&model=` | Матрица (ниже) |
| `/api/admin/users/{id}/mixes?limit=30` | Миксы пользователя: `id, created_at, closed_at, status, title, mode, taste_model, size, downloaded, launches, started, heard, finished, early_skips, likes, dislikes, top_generators` |
| `/api/admin/mixes/{id}` | Микс целиком (ниже) |
| `/api/admin/users/{id}/waves?from=&to=&origin=&seed_kind=&limit=&offset=` | Сессии волны: `session_id, created_at, last_activity_at, origin, client, restart_of, seed_kind, seed {title, artist, ref, nd_song_id, artist_ref, nd_artist_id}, mode, taste_model, next_calls, handed, started, heard, finished, early_skips, likes, dislikes, listened_ms, downloads, reconstructed`; плюс сводка `by_origin`, `by_seed_kind` |
| `/api/admin/waves/{sid}` | Сессия целиком: всё из строки списка + `gens` (генератор → треков, секунд, ошибка), выданные треки (`wave_items` по `position`) с итогом каждого (`not_played` / `started` / `heard` / `finished` / `early_skip at Xs` / `liked` / `disliked`), лента событий (`ts, kind, track, position_s, client`) |

Параметры периода: `from`/`to` — местные даты включительно, по умолчанию последние 30 дней, не больше 3 лет;
плохие даты → 422 (как в `/api/plays/summary`). Пользователь не найден → 404, микс/волна чужого — не важно (админ видит
всех), неизвестный id → 404.

**`/listening` — ответ (сокращённо):**

```json
{
  "period": {"from": "2026-10-01", "to": "2026-10-31", "timezone": "Europe/Moscow"},
  "totals": {"counted_listens": 412, "heard_events": 437, "starts": 690, "finished": 301, "early_skips": 160,
             "late_skips": 70, "listened_ms": 81234000, "unique_tracks": 220, "unique_artists": 61,
             "new_tracks": 74, "new_artists": 12, "active_days": 24, "likes": 18, "unlikes": 3,
             "dislikes_track": 2, "dislikes_artist": 0, "downloads": 9},
  "by_day": [{"date": "2026-10-01", "heard": 14, "listened_ms": 2900000, "by_source": {"wave": 8, "mix": 4, "search": 2}}],
  "by_source": [{"source": "wave", "launches": 12, "starts": 210, "heard": 150, "finished": 101,
                 "early_skips": 40, "listened_ms": 0}],
  "heatmap": [[0, 0, 1, …24 значения…], …7 строк, понедельник первым…],
  "clients": [{"client": "app/1.12.0", "devices": 1, "starts": 500, "heard": 330, "last_at": "…"}],
  "top_tracks": [{"key": "nd:…", "artist": "…", "title": "…", "heard": 9, "listened_ms": 1900000}],
  "top_artists": [{"artist": "…", "heard": 40, "tracks": 11}],
  "discoveries": [{"key": "…", "artist": "…", "title": "…", "first_heard_at": "…", "source": "wave"}],
  "recommendations": {"mix": {"built": 30, "launched_mixes": 21, "launches": 33, "started": 520, "heard": 340,
                              "finished": 250, "early_skips": 110, "likes": 9, "dislikes": 1,
                              "familiar": {"started": 200, "heard": 160}, "new": {"started": 320, "heard": 180},
                              "by_generator": [{"generator": "ytm_radio", "title": "Похожее: радио YouTube Music",
                                                "offered": 410, "started": 120, "heard": 80, "finished": 60,
                                                "early_skips": 25, "liked": 4}],
                              "adopted": 7},
                      "wave": {"launches": 12, "restarts": 2, "by_origin": {"home_card": 5, "track_menu": 4, "radio": 3},
                               "by_seed_kind": {"own": 5, "track": 4, "library_artist": 3},
                               "started": 210, "heard": 150, "finished": 101, "early_skips": 40, "likes": 6,
                               "by_generator": […], "adopted": 5}},
  "quality": {"events_without_context": 12, "events_without_playback": 300, "scrobbles": 20,
              "legacy_finishes": 15, "inferred_launches": 9, "reconstructed_waves": 5, "first_event_at": "…"}
}
```

`offered` у генератора микса — сколько его песен было в 50 активных; у волны — сколько выдано (`wave_items`).
`adopted` — прижившиеся рекомендации (§5). `heatmap` — засчитанные прослушивания по местному дню недели и часу.

**`/api/admin/mixes/{id}` — ответ:** заголовок микса (`title, description, mode, taste_model, created_at,
closed_at, status, nd_playlist_id`), `funnel`:

```json
{"generators": [{"generator": "ytm_radio", "title": "…", "tracks": 50, "used": 14, "seconds": 1.2, "error": null, "off": false}],
 "candidates": 640, "rejected": {"disliked": 3, "version": 21, "duration": 9, "recent": 40, "negative_score": 0},
 "kept": 567, "sound_scored": 300, "active": 50, "familiar": 20, "new": 30, "reserve": 20,
 "launches": 3, "started": 31, "heard": 22, "finished": 17, "early_skips": 9, "liked": 2, "disliked": 0}
```

`seeds` (из `stats_json.seed_keys` с именами и гранью), `facets` (из `stats_json.facets` с именами исполнителей),
`maturity`, `embedder`, и `items` (active, затем reserve, failed): `position, state, familiar, generator,
generator_title, gens, score, score_parts` (для миксов до этапа 1 частей нет — вернуть `null`, **не**
пересчитывать по текущему конфигу), `sim, rank, profile` (снимок, если есть), `downloaded_for_mix`, `cleanup`,
`outcome` (`not_played` / `started` / `heard` / `finished` / `early_skip` / `liked` / `disliked` — по событиям этого
микса; отдельно флаг «слушал этот трек вне микса, пока микс действовал»). У старых миксов без `rejected` — `null`.

**`/api/admin/taste/matrix` — ответ:**

```json
{"model": "v2", "users": [{"id": 1, "username": "…", "name": "…"}],
 "rows": [{"key": "nd:…", "artist": "…", "title": "…", "owned": true,
           "cells": {"1": {"s": 4.1, "p": 4.6, "n": 0.5, "liked": true, "disliked": false, "heard": 7}},
           "sum_pos": 4.1, "min": 4.1, "max": 4.1, "users_positive": 1, "users_negative": 0}],
 "total": 512, "offset": 0}
```

- Профили всех пользователей Хаба (кроме исключённых) считаются по одному `now`. Строки объединяются **только по
  проверенным псевдонимам**: union-find по `ref` и `nd:<song>` треков (`TasteTrack.ref`, `nd_song_id`); по отпечатку
  «исполнитель|название» **не объединять** (разные версии). Ключ строки — `nd:<song>`, если есть, иначе `ref`.
- `filter`: `all`, `shared_liked` (s > 0 у ≥ 2), `conflict` (у одного s ≥ 1, у другого s ≤ −1), `single`
  (ненулевой вес у одного), `disliked` (явное 👎 у кого-то). `sort`: `sum` (Σ положительных, по умолчанию),
  `spread` (max − min), `title`, `user:<id>`.
- `by=artist` — то же по `A_artist` (ключ — нормализованное имя `artist_key`, подпись — `artist_names`).

### 7.7. Производительность

Данных мало (§2). Считать на запрос; без кэша. Ограничения: `limit ≤ 200`, матрица считает профили один раз на
запрос. Замерить на копии рабочей БД (§9.5): цель — ответ любого эндпоинта < 1 с при текущем объёме; если матрица
или `listening` медленнее — простой кэш в памяти на 60 с по ключу `(user_id, model, max(play_events.id),
max(dislikes.id))`. Запросы к `play_events` — с фильтром по `user_id` и `ts` (индекс `ix_play_events_user_ts`).

### 7.8. Тесты этапа 2

Синтетические пользователи и события, явный `now`, timezone; без сети (respx не должен видеть ни одного запроса —
проверить, что `respx` в режиме «запрещено всё» не ловит обращений).
- 403 для не-админа на каждом новом пути; 404 на неизвестных id.
- Разложение: сумма `value` по стороне P даёт `positive_raw`, по N — `negative`; `s = sat(P) − N`; записи с причинами
  (`w < 0.01`, «старше 3 лет», `start`, ♥ импорта не учтён); профиль с trace == без trace.
- Профиль админа совпадает с тем, что строит сборка микса для того же `now` (`build_profile(model="v2")`).
- Запуски: `launch_id`; волна по строке, по выжимке и восстановленная; эвристика (смена контекста, разрыв 30 мин,
  два устройства параллельно); `playlist:<id>` и старый `playlist` → микс; перезапуск не считается запуском.
- `listening`: засчитанные прослушивания совпадают с `listening_summary.summary` за тот же период; heatmap в местном
  времени (переход через полночь); открытия; прижившиеся рекомендации.
- Матрица: объединение по псевдонимам двух пользователей (у одного трек известен только по `ref`, у другого — по
  `nd:` с проверенной парой), фильтры и сортировки, исключённый пользователь.
- Микс: воронка из `stats_json`, итоги песен, старый микс без `score_parts`/`rejected` → `null`.
- Ни один эндпоинт не пишет в БД (сравнить `total_changes` соединения SQLite до/после или счётчики строк).

### 7.9. Документы, выкатка, приёмка

`hub/API.md` — новый раздел «Admin insights» (пути, параметры, поля, коды `source`/`origin`/`seed_kind`/`outcome`);
`INSIGHTS.md` — «как устроено» для этапа 2; `PROGRESS.md`. Выкатка Хаба (`CLAUDE.md` п. 7).
**Готово, когда:** все эндпоинты отвечают на копии рабочей БД, ничего не пишут, укладываются в цель §7.7, тесты зелёные.

### 7.10. Как устроено (этап 2 сделан 2026-10-31)

- Пакет `hub/app/insights/`, роутер `hub/app/api/insights.py` (`prefix="/api/admin"`, `Depends(admin_user)` у каждого
  пути, `Cache-Control: no-store` зависимостью роутера), схемы — раздел `# --- admin insights` в `api/schemas.py`
  (строки списков типизированы, сводные блоки — словари с ключами из `hub/API.md`, «Admin insights»).
- `taste_math.calculate_profile(..., trace=None)` (и `calculate_baseline`) — `Contribution`/`Trace`. Запись в каждой
  точке изменения `positive_raw`/`negative`: события start/finish/skip, собственный ♥ (за окном 3 лет — сторона «0»,
  «старше 3 лет»), «Не нравится» из журнала и из таблицы `dislikes` (`dislike_legacy`), загрузка и ♥ альбома (вклад 0),
  плюс отдельный проход `_ignored` после расчёта: ♥ импорта («импорт — не свой»), тестового клиента и снятые ♥.
  `value` у стороны N — положительная величина (Σ P = `positive_raw`, Σ N = `negative`). Trace только наблюдает:
  тест — профиль с `Trace()` равен профилю без него; `calculate_offline` его не получает.
- `taste_view.admin_profile` = `load_snapshot` + `calculate_profile` (модель `cfg.mixes.taste_model`) или
  `calculate_baseline` (`model=v1`, «локальный v1»). Тест: равен `build_profile(model="v2")` сборки микса. Грани —
  `taste_select.prepare` под `wait_for(8)` только при настроенном embedder; иначе `facets: null` и `embedder:
  "off"/"down"/"timeout"`. Сиды «на сегодня» — `V2Profile.seeds()` (= `select_profile_seeds`), резерв онбординга —
  `round((1 − m)·n)` и список исполнителей как в `taste_select.seeds`, без `enrich_selected`/`cold_seeds`.
- `names.resolve` — пакетно, без сети: `Track` по `nd_song_id`/`deezer_track_id` → имя из профиля → последнее
  `track_metadata` события → `catalog_cache` (`mix:track:`, `dz:track:`) → `mix_items.features_json.track` →
  `wave_items` → «Неизвестный трек». `owned` — есть строка `Track` с песней.
- `launches.load(s, user_id, now)` читает один раз события пользователя (без `superseded_scrobble`, тестовых клиентов,
  `ts > now`), сессии волны, `wave_items`, миксы с песнями и строит **попытки** (`(device_id, playback_id)`; старые —
  `start` + следующий `skip`/`finish` того же трека на том же клиенте; засчитанное — по `listened_ms`, как
  `listening_summary`, плюс scrobbles Navidrome) и **запуски**: по `launch_id`; волна — по строке `wave_sessions`
  (`wave:<sid>` в `launch_id` или `context`), без строки — `reconstructed`; остальное — эвристика по потоку
  (`device_id`, иначе `client`): новый запуск при `start` с другим контекстом или разрыве > 30 мин. Scrobbles в
  запуски не входят. Привязка к миксу: `mix:<id>`, `playlist:<id>` = `nd_playlist_id` действовавшего микса,
  старый `playlist` — ≥ 60 % начатых треков из активных песен действовавшего микса (`inferred_playlist`).
  ♥/👎 относятся к запуску по `launch_id`/волне или по тому же контексту до конца запуска + 5 мин.
- «Прослушано» у попытки = засчитано по checkpoint'ам; у попытки без них (старая история) — по событиям (finish,
  skip ≥ 30 с). `totals.counted_listens` и `listened_ms` совпадают с `/api/plays/summary` (тест).
- `analytics.listening` / `overview`, `mixes_view`, `waves_view` — по этой истории; `seed_kind` выводится из
  `request_json.seed`, у старых строк — из `seed_json`. Миксы до этапа 1: `rejected`, `score_parts` — `null`.
- Конфиг: `analytics.exclude_users` (имена, по умолчанию пусто) — вне общих итогов и матрицы, `include_excluded=true`
  возвращает. `facet_cos` у трека и карта появились в этапе 3 (§8.3).
- Тесты `hub/tests/test_insights.py`: trace (суммы, причины, равенство профилей), запуски (по id, по строке волны и
  выжимке, восстановленная, перезапуск, эвристика: контекст, разрыв, два устройства; `playlist:<id>` и старый
  `playlist`), `listening` = `summary` и локальный час через полночь, рекомендации миксов/волны, матрица (псевдонимы,
  фильтры, сортировка по пользователю, QA-пользователь), микс (воронка, итоги, старый микс → `null`), волны, обзор;
  403 на каждом пути, 404/422; ни одной записи (SQL-шпион на движке) и ни одного запроса в сеть (`respx` без маршрутов).
- Замер на копии рабочей БД (3 пользователя, ~1,9 тыс. событий у самого активного, ~360 треков в профиле, 11 миксов,
  29 сессий волны; копия в контейнере, как рабочий том): самый медленный ответ — `taste/track` 0,44 с и матрица 0,38 с,
  остальное ≤ 0,3 с; записей в БД — 0. Кэш не нужен. Копия удалена.

---

## 8. Этап 3 — проекция для карты (embedder + Хаб) — сделан 2026-10-31 (§8.3)

Ветка, например, `feat/insights-map-projection`. `embedder/` + `hub/`.

### 8.1. embedder: `POST /v2/project`

Тяжёлая математика — там, где уже лежат векторы и numpy. Модель не загружается.

```text
POST /v2/project
{ "space_id": "…",
  "keys": ["nd:…", "dz:…", …],                 // до 5000
  "facets": [{"id": "…", "center": [...]}],     // до 5, центры из /v2/profile
  "basis": "global" | "keys",                    // по умолчанию "global"
  "fit_keys": ["…"] }                            // для basis="keys": по каким ключам строить оси (до 5000)
→ { "model", "space_id", "dim", "basis",
    "explained": [0.11, 0.07],                   // доля дисперсии двух осей
    "points": {"key": [x, y]},
    "facets": {"id": [x, y]},
    "facet_cos": {"key": [cos к грани 1, cos к грани 2, …]},   // в порядке facets запроса
    "missing": [...], "diagnostics": {...} }
```

- Векторы — центрированные `v′` текущего снимка (как `/v2/vectors`). Оси — PCA (SVD) двух главных компонент:
  `global` — по **всем** корректным векторам снимка (карты разных пользователей сопоставимы между собой и стабильны,
  пока не изменился снимок), кэш осей в памяти по `space_id` (сбрасывается вместе со снимком); `keys` — по
  `fit_keys` (личная раскладка, крупнее детали вкуса).
- Знак каждой оси детерминирован: компоненту умножить на −1, если её координата с наибольшим |значением|
  отрицательна (равенство — меньший индекс). Координаты точки: `(v′ − mean) · axes`.
- `facet_cos` — косинус `v′` к каждому центру грани (та же величина, что `pos` в `/v2/similarity`, но по каждой грани).
- Ошибки как у `/v2/*`: старый `space_id` → 409 `stale_space`; нарушение лимитов → 422; ключи без вектора → `missing`.
- Тесты `embedder/tests` на синтетических векторах: детерминизм (дважды → одинаково, порядок ключей не важен), знак
  осей, `explained` в [0, 1] и по убыванию, `global` не зависит от состава `keys`, `keys` зависит от `fit_keys`,
  409/422, `missing`, проекция центров граней совпадает с проекцией точки в центре. Прогон — как README «Сервис embedder».
- `MIXES.md` §4.2 — строка в таблице v2-методов.

### 8.2. Хаб: клиент и сборка карты

- `hub/app/mixes/embedder.py`: `project_v2(keys, profile: GeometryProfile, *, basis, fit_keys)` → типизированный
  ответ; 404 старого сервиса/недоступность → `EmbedderDown`, 409 → `EmbedderSpaceExpired` (один повтор с новым
  `/v2/profile`, затем «карта недоступна»).
- `hub/app/insights/maps.py` + эндпоинт **`GET /api/admin/users/{id}/taste/map?model=&basis=global|user&mix=&wave=&limit=600`**:
  - Слои ключей: `profile` — треки с `s ≠ 0` и все ♥/загрузки/👎, первые `limit` по `|s|` плюс все сиды и явные 👎;
    `mix` — активные песни микса (`mix` или текущий); `wave` — `wave_items` выбранной сессии (по умолчанию последней).
  - Ответ: `available` (+ `reason`, если нет), `space_id`, `basis`, `explained`, `facets: [{id, weight, artists, x, y,
    color_index}]`, `points: [{key, artist, title, layers: ["profile","mix","wave"], x, y, fx, fy, s, p, n, facet_id,
    facet_cos[], seed, liked, disliked, negative, familiar, generator, mix_position, wave_position, outcome}]`,
    `missing: [{key, artist, title, layers}]` (нет вектора — показываются списком «без звука»), `truncated`.
  - `fx, fy` — раскладка «Грани» считается в Хабе из `facet_cos`: якоря граней на окружности радиуса 1 (по убыванию
    веса, по часовой от 12 часов); `u_f = max(0, cos_f)²`; точка = `Σ u_f·A_f / Σ u_f · r`, `r = clamp(max cos / 0.6, 0, 1)`;
    `Σ u_f = 0` → центр. Константы — в коде с комментарием; эта раскладка честно отражает метрику рекомендаций
    (близость к граням), в отличие от PCA.
  - `facet_id` точки — грань с наибольшим `cos` (для членов грани — сама грань из `members`).
- Разложение трека (§7.6 `/taste/track`) получает `facet_cos` из `/v2/project` одного ключа.
- Тесты Хаба: respx-моки `/v2/profile` и `/v2/project`; `available=false` при отказе; слои и усечение; раскладка
  граней (точка на якоре, точка без положительных cos — в центре).

**Выкатка:** Хаб и embedder. embedder — `docker compose up -d --build embedder` (образ ~7 ГБ: собирать, когда ПК
свободен), затем `docker image prune -f` (`CLAUDE.md`, «Команды»). Проверка: `embedder` `healthy`, `/v2/project`
отвечает из сети compose.

### 8.3. Как устроено (этап 3 сделан 2026-10-31)

- **embedder** `POST /v2/project` (`embedder/app/main.py`, математика — `geometry.py`: `fit_axes`, `project`,
  `facet_cosines`). Оси — собственные векторы ковариации центрированных `v′` (`eigh`, ключи отсортированы, поэтому порядок
  запроса не влияет); `global` — по всем векторам снимка, кэш одной записи в `Geometry` по `space_id` (сбрасывается вместе
  со снимком); `keys` — по `fit_keys`. Знак оси — наибольшая по модулю координата положительна. Ось без дисперсии нулевая,
  её доля 0. `basis=keys` без `fit_keys` — 422. Центры граней проецируются как точки. Тесты — `embedder/tests/test_project.py`.
- **Хаб**: `EmbedderClient.project_v2(keys, profile, basis=, fit_keys=)` → `GeometryProjection` (ответ проверяется на
  покрытие запрошенных ключей, число граней и размерность 2; 404/недоступность/кривой ответ → `EmbedderDown`, 409 →
  `EmbedderSpaceExpired`). `insights/maps.py`: `project()` — один повтор с новым `/v2/profile` при 409, затем причина
  `stale_space`; `build()` — карта (слои профиль/микс/волна, схлопывание псевдонимов ключей до `TasteTrack.key`, имена
  без сети, `missing`, раскладка «Грани» `anchors`/`facet_layout`, константа `FACET_FULL = 0.6`); `track_facet_cos()` —
  `facet_cos` в `/taste/track`. `basis=user` подгоняет оси по ключам с положительным весом (те же, что идут в
  `/v2/profile`), без них — `global`. Сиды на карте — сегодняшние (`p.seeds`), как в сводке вкуса.
- Контракт ответа — `hub/API.md`, «Admin insights», `/taste/map`. Причины недоступности: `model_v1` (v1 без граней),
  `embedder_off|down|timeout|stale_space`. Карта читает только БД Хаба и embedder (`/v2/profile`, `/v2/project`), ничего не пишет.
- Тесты `hub/tests/test_insights_map.py` (respx): слои и усечение, `missing`, личная база и выбор микса/волны, 404/403,
  недоступность (нет embedder, v1, 404/503, двойной 409), повтор после 409, `facet_cos` трека, раскладка «Грани» (точка на
  якоре, без положительных cos — в центре, две грани — посередине), клиент (тело запроса, ошибки, покрытие ключей).

---

## 9. Этап 4 — экраны админа в PWA — сделан 2026-10-31 (§9.7)

Ветка, например, `feat/insights-pwa`. Только `web/` (+ документы). Данные — только из API этапов 2–3.

### 9.1. Маршруты (все в `ROUTES` `web/src/App.tsx`, вкладка `more`, и в браузере, и в приложении)

| Маршрут | Экран |
|---|---|
| `/admin` | существующий; сверху две строки-входа: «Аналитика пользователей» и «Веса песен у всех» |
| `/admin/insights?from=&to=` | обзор всех пользователей |
| `/admin/taste?by=&filter=&q=&sort=` | матрица песни × пользователи |
| `/admin/user/:id?tab=overview\|listening\|taste\|map\|mixes\|waves&from=&to=…` | пользователь с вкладками (сейчас — одна страница; её содержимое становится вкладкой «Обзор») |
| `/admin/user/:id/track?key=` | разложение веса трека (ключ с `:` — в запросе, `encodeURIComponent`) |
| `/admin/mix/:id` | путь рекомендаций микса |
| `/admin/wave/:sid` | сессия волны |

Вкладка, период, фильтры и слои карты — **в адресе** (страница переживает перезагрузку новой сборкой,
`lib/update.ts`; смена query не считается переходом). Никакого воспроизведения с этих экранов не нужно.

### 9.2. Графики — свои компоненты, без библиотек

Папка `web/src/components/charts/`; всё на SVG с `viewBox` и ширине контейнера; цвета — только токены CSS;
подписи по-русски, числа `toLocaleString('ru-RU')`; у каждого графика есть табличная/списочная альтернатива на том же
экране (карта — не единственный способ увидеть данные).

- `BarsByDay` — столбцы по дням, стек по источникам; тап по столбцу — подсказка с числами.
- `ShareBar` — горизонтальная 100 %-полоса долей (источники, места запуска, сиды) + легенда.
- `Heatmap` — 7 × 24 (день недели × час), интенсивность — прозрачность акцента.
- `Funnel` — ступени воронки (горизонтально на десктопе, вертикально на телефоне), у каждой — число и доля от прошлой.
- `DivergingBar` / `WeightCell` — расходящаяся шкала для `s` и `A_artist`: положительное — `--color-accent`,
  отрицательное — `--color-danger`, насыщенность по `|s| / max`; число всегда видно текстом (не только цвет).
- `ScatterMap` — карта:
  - SVG до ~1500 точек; точки рисовать от больших к малым; попадание по тапу — ближайшая точка в радиусе 12 px
    (расчёт в координатах данных, один обработчик на SVG, а не на каждую точку);
  - перетаскивание (pointer events), колесо на десктопе, кнопки «+», «−», «Сбросить»; на телефоне — те же кнопки
    (жест «щипок» — по желанию);
  - тап по точке → `Sheet`: исполнитель — название, `s`, P/N, грань, флаги, «слои» (в миксе №, выдан волной, итог),
    ссылка «Почему такой вес» → `/admin/user/:id/track?key=`;
  - поиск по названию подсвечивает совпадения.
- Новые токены в `web/src/index.css` для обеих тем (тёмная, `hm-light`): `--color-facet-1` … `--color-facet-5`
  (различимы между собой и на фоне в обеих темах), нейтральный «вне граней» — `--color-dim`.
  Помнить подводный камень Tailwind 4 из `CLAUDE.md` (`!text-accent` у `IconButton`).

### 9.3. Экраны (телефон 375 px; на десктопе те же блоки шире, в две колонки, где помещается)

**Обзор пользователей `/admin/insights`**

```
← Аналитика пользователей               [7 дн | 30 дн | 90 дн | Всё]
┌────────┐┌────────┐┌────────┐
│ 1 204  ││ 61 ч   ││ 38     │   прослушиваний · время · запусков микса
└────────┘└────────┘└────────┘   (+ запусков волны, новых песен, ♥)
Прослушивания по дням            ▇▅▆█▃… (стек по пользователям)
По пользователям   (таблица, первый столбец закреплён, прокрутка внутри)
 Имя     Прослуш.  Время  Микс  Волна  ♥  👎  Новые  Активн.дн  Версия
 anna       980    48 ч    30    12   18  2    74      24     app/1.12
 …   тап по строке → /admin/user/:id?tab=listening
```

**Пользователь — вкладка «Прослушивания»**

```
← anna                          [Обзор][Прослушивания][Вкус][Карта][Миксы][Волна]
[7 дн | 30 дн | 90 дн | Всё]
плитки: прослушиваний · время · дослушано % · ранних пропусков % · новых песен · активных дней
По дням: столбцы, стек по источникам (Волна / Микс / Свои списки / Поиск / Другое)
Источники: ShareBar + таблица (запуски, прослушиваний, дослушано %, ранние пропуски %)
Рекомендации:  Микс — собрано 30, запускал 21 раз(а), 33 запуска, 340 прослушано, прижилось 7
               Волна — 12 запусков (+2 перезапуска): Главная 5 · Меню трека 4 · Радиостанции 3
                       сиды: своя 5 · по треку 4 · по исполнителю 3
Генераторы (микс и волна): таблица «предложено · начато · прослушано · ранние пропуски · ♥»
Когда слушает: Heatmap 7×24
Топ песен / Топ исполнителей / Открытия (списки по 10, «Показать все»)
♥ и 👎 за период (лента)
Клиенты и устройства: app/1.12.0 (1 устройство), pwa …
Качество данных: Notice — «N запусков определены приблизительно (старые события без id запуска)»,
                 «M сессий волны восстановлены по событиям — с чего запускали, неизвестно»
```

**Вкладка «Вкус»**

```
[Модель: v2 | v1 (локальный)]          посчитано 31.10 14:05
Зрелость 0,82 · опыт E=16,4 · активных единиц 41 · треков: +128 / −17 · ♥ 30
Грани вкуса:  ■ 46% Кино, Сплин (38)   ■ 31% MACAN, Баста (25)   ■ 23% …   (embedder нет → Notice)
Сиды сегодня (8): список с цветом грани; «2 места — онбординг»
Исполнители: DivergingBar A_artist (топ 20 + отрицательные)
Треки:  [Все][Положительные][Сиды][Отрицательные][♥][👎][Скачанные]  🔍  сортировка ▾
  Кино — Группа крови        s ████ 4,12   P 4,6 · N 0,5   ♥ 🔁7
  …  тап → разложение
```

**Разложение веса `/admin/user/:id/track?key=`**

```
← Кино — Группа крови
s = sat(P) − N = sat(5,30) − 0,50 = 4,49 − 0,50 = 3,99
♥ с 12.10 · дослушан 7 раз · ранних пропусков 1 · знакомый · может быть сидом
Вклады (новые сверху):
 30.10 21:14  дослушал (финиш)        +2 × 0,93 = +1,86   wave · app/1.12
 28.10 08:02  пропуск на 12 с          −1 × 0,88 = −0,88 (N)  mix:14
 12.10        ♥                       +1 × 0,61 = +0,61
 …           «старше 3 лет» / «w < 0,01» — серым
Исполнитель: A_artist = 6,1 (32 прослушивания)
Близость к граням: ■ 0,71 ■ 0,22 ■ 0,05
В рекомендациях: Микс 31.10 №3 (ytm_radio, 2,41) — дослушан; Волна 29.10 — пропуск на 40 с
```

**Вкладка «Карта»**

```
Раскладка: [Звук (PCA) | Грани]      Оси: [Общие | Личные]   (оси — только для PCA)
Слои: [✓ Вкус] [✓ Микс дня 31.10] [✓ Волна 30.10 «По треку: …» ▾]
┌───────────────────────────────┐
│   ·  •  ●      ◆        +  −  │   ● размер ∝ √|s|, цвет — грань
│ ●  ◎   ●  ◆◆      ×           │   ◎ сид (кольцо), × явное 👎, ○ красный контур — s<0
│    ◆→◆→◆ (путь волны по порядку) │   ◆ микс: заливка — знакомое, контур — новое
│   ★ Кино, Сплин  ★ MACAN, Баста │   ★ центр грани с подписью
└───────────────────────────────┘
2D сохраняет 18% разброса звука: расстояния приблизительные, точная близость — «Грани».
Без звука: 23 трека (список)
```

Волна на карте — точки `wave_items` по порядку выдачи, соединённые тонкой линией, номер у точки; итог трека —
значок (дослушан / пропущен / не сыгран). Это и есть «карта рекомендаций»: видно, из каких граней вкуса пришли
рекомендации и куда волна уходила по ходу сессии.

**Вкладка «Миксы» и `/admin/mix/:id`**

```
Миксы: список — дата · название · режим · «запускал 3 раза · 22 из 50 прослушано · ♥2»
/admin/mix/:id:
Воронка: Генераторы 9 → Кандидаты 640 → После фильтров 567 (отсеяно: давно/недавно 40, версии 21, …)
         → В миксе 50 (20 знакомых / 30 новых) → Начато 31 → Прослушано 22 → Дослушано 17
Сиды (8) с гранями · Грани
Генераторы: дал · взято в микс · начато · прослушано · ранние пропуски · ♥
Песни: № · трек · генератор · знак./нов. · оценка (стек частей: генераторы/звук/популярность/штрафы) · итог
Запас и не скачанные — свёрнуто; очистка: удалено N, оставлено M (причины)
```

**Вкладка «Волна» и `/admin/wave/:sid`**

```
Сводка: ShareBar по месту запуска и по виду сида; перезапуски отдельно
Сессии: 30.10 21:10 · 🏠 Главная · своя · 18 начато · 12 прослушано · ранних 4 · ♥1
        29.10 08:00 · ⋯ Меню трека · по треку «Сплин — Выхода нет» · 9 · 7 · 1
        27.10 —     · не записано · восстановлено по событиям
/admin/wave/:sid: место, клиент, сид, режим, модель, длительность, вызовов next;
генераторы (треков, секунд, ошибки); лента выданных треков по порядку с генератором, знак./нов., sim и итогом
(дослушан / пропуск на 12 с / ♥ / 👎 / не сыгран — заменён)
```

**Матрица `/admin/taste`**

```
[Треки | Исполнители]  [Все][Общие любимые][Спорные][Только у одного][С 👎]  🔍
           anna    boris  vera    Σ+
Кино — …   4,1     2,0     —     6,1     (ячейка — WeightCell; тап → разложение этого пользователя)
```

### 9.4. Мок

`web/mock/plugin.ts` — ответы на все новые пути с **синтетическими** данными (детерминированный генератор): 3
пользователя, ~80 треков с весами разных знаков, 3 грани, микс с воронкой и итогами, 4 сессии волны с разными
`origin`/сидами (одна `reconstructed`, одна `restart`), события за 30 дней для графиков; `available=false` для карты
у одного пользователя (проверка заглушки). Мок-админ уже `is_admin: true`.

### 9.5. Проверка

- `npm --prefix web run build` (typecheck).
- Конфигурация `web-mock` из `.claude/launch.json` (пароль `test`): каждый экран на 375 px и на десктопе, тёмная и
  светлая тема (класс `hm-light`), встроенный режим (`/?embedded=1#/admin/...`); нет горизонтальной прокрутки
  страницы; таблицы прокручиваются внутри; карта: перетаскивание, масштаб, тап по точке, слои, обе раскладки.
- Изолированно на реальных данных (без входа под владельцем): сделать копию БД средствами SQLite (`.backup`) в папку
  вне репозитория (scratch/temp), поднять на ней Хаб/функции этапов 2–3 локально и вывести **только агрегаты и
  время ответа** (не названия треков и не события) — убедиться, что расчёт не падает на реальной истории и
  укладывается в §7.7. Копию удалить после проверки.
- На рабочем Хабе экраны смотрит владелец (в чате попросить открыть `/admin` и проверить). Под владельцем не входить;
  QA-аккаунту права админа не давать без явного разрешения владельца.
- Ручные скриншоты — только в `design-review/shots/` или `design-review/after/` (в git не идут).

### 9.6. Документы, выкатка, приёмка

`INSIGHTS.md` («как устроено»: экраны, раскладки, легенда, ограничения 2D), `PROGRESS.md`. Выкатка Хаба (PWA
собирается в образе), проверка `/healthz` с новым `web_build`. **Готово, когда:** все экраны работают на моке на
обеих ширинах и темах, сборка зелёная, Хаб выкачен.

### 9.7. Как устроено (этап 4 сделан 2026-10-31)

- **Код.** Типы и вызовы API — `web/src/api/insights.ts` (`insightsApi`; поля — по `hub/app/insights/*.py`, а не по плану:
  например, в `/taste/track` поле `artist` — блок исполнителя, имя трека — `title` + `artist.name`). Экраны —
  `web/src/pages/insights/`: `Overview.tsx` (`/admin/insights`), `Matrix.tsx` (`/admin/taste`), `Listening.tsx`,
  `Taste.tsx`, `MapTab.tsx`, `Recs.tsx` (вкладки «Миксы»/«Волна», `/admin/mix/:id`, `/admin/wave/:sid`), `Track.tsx`
  (`/admin/user/:id/track?key=`), общие блоки — `common.tsx` (плитки, период, `ScrollTable` с закреплённым первым
  столбцом и прокруткой внутри, «Показать все»). `AdminUser.tsx` — оболочка с вкладками, прежняя страница — «Обзор».
  Маршруты — `ROUTES` в `App.tsx` (вкладка `more`, и в браузере, и в приложении); `/admin` — две строки-входа.
  На маршрутах `/admin/*` `main` шире (`max-w-6xl`): на десктопе блоки встают в две колонки (`Columns`, `lg:`).
- **Адрес.** Вкладка `tab`, период `p` (`7|30|90|all`, по умолчанию 30 дней; `from`/`to` считаются в
  `lib/insights.ts`, «Всё» — предел API в 3 года), `model`, `filter`, `sort`, `q`, `by`, у карты `layout`, `basis`,
  `layers`, `mix`, `wave`. Меняются `setQuery` — `replaceState` без записи в историю и без прокрутки наверх
  (`navigate` прокручивает); перезагрузка новой сборкой (`lib/update.ts`) открывает тот же экран с теми же фильтрами.
  Ключ трека — только в query, `encodeURIComponent` (`trackLink`).
- **Подписи кодов** — `lib/format.ts`, раздел admin insights: источники, места запуска волны (`not_recorded`/`null` →
  «не записано»), сиды, место запуска списка, способ определения запуска (`inferred` → «приблизительно»,
  `reconstructed` → «восстановлено по событиям»), итоги трека, вклады веса, причины отсева, причины недоступности карты.
- **Графики** — `web/src/components/charts/`, свой SVG без зависимостей, цвета только токенами: `BarsByDay` (стек по
  источникам или пользователям, тап — числа дня), `ShareBar`, `Heatmap` 7×24, `Funnel` (вертикально на телефоне,
  в ряд с `md:`), `DivergingBar`/`WeightCell` (плюс — `--color-accent`, минус — `--color-danger`, насыщенность по
  `|s|/max`, число всегда текстом), `ScatterMap`. Токены `--color-facet-1…5` — в `@theme` (тёмная) и в
  `:root.hm-embedded.hm-light`; «вне граней» — `--color-dim`; цвет грани — `color_index` (ранг по весу) → токен.
  У каждого графика рядом таблица или список (у карты — «Список точек» и вкладка «Вкус»).
- **Карта.** `ScatterMap` — viewBox 400×320, свой перенос и масштаб (точки не растут при приближении), один обработчик
  pointer-событий на SVG: сдвиг больше 5 px — перетаскивание, иначе тап → ближайшая точка в радиусе 12 px экрана;
  колесо (non-passive) и кнопки «+», «−», «Сбросить»; щипка нет. Точки рисуются от больших к малым. Легенда: ● вкус
  (размер ∝ √|s|, цвет — грань), ◎ сид, красный контур — s < 0, × 👎, ◆ микс (заливка — знакомое, контур — новое),
  ■ с номером — волна по порядку выдачи и линия пути, ★ центр грани с подписью. Раскладки «Звук (PCA)» (`x, y`; оси
  «Общие»/«Личные» = `basis=global|user`) и «Грани» (`fx, fy` из Хаба); подпись о доле объяснённой дисперсии.
  Слои вкус/микс/волна, выбор микса и сессии волны (списки `/mixes`, `/waves`), поиск подсвечивает совпадения,
  «Без звука» — список `missing`. Тап → `Sheet`: s, P, N, грань, флаги, № в миксе / в волне, итог, «Почему такой
  вес». `available=false` — пояснение причины вместо карты.
- **Обзор пользователей** в API без разбивки по дням: столбцы по дням собираются из `/listening` каждого пользователя (того же
  периода), стек по пользователям.
- **Мок** — `web/mock/insights.ts` (подключён в `plugin.ts`): детерминированная синтетика — 3 пользователя, 82 трека
  с весами обоих знаков, 3 грани, 3 микса (самый старый — без `rejected`/`score_parts`), 5 сессий волны (одна
  `restart`, одна `reconstructed`, одна `cleared`), дни за период; у пользователя 3 карта `available=false`
  (`embedder_down`) и грани `null`.
- **Проверено** на `web-mock`: все экраны на 375 px и 1280 px, тёмная и светлая (встроенный режим, `hm-light`) темы,
  `/?embedded=1#/admin/...`, без горизонтальной прокрутки страницы (scrollWidth = clientWidth на всех маршрутах),
  таблицы прокручиваются внутри; карта — тап, «+», перетаскивание, обе раскладки, слои. На рабочем Хабе экраны смотрит
  владелец (под владельцем агент не входит). Изолированный прогон на копии БД не повторялся: API не менялся с этапов 2–3,
  где он сделан (§7.10).
- **Ограничения.** Экраны микса и волны не знают пользователя (в ответе нет `user_id`), поэтому их песни не ведут на
  разложение веса — оно открывается из вкладок «Вкус», «Карта» и матрицы. «Лента ♥ и 👎» — только счётчики периода
  (событий ♥ по одному API не отдаёт). 2D-карта приблизительна; точная близость — раскладка «Грани».

---

## 10. Этап 5 — приложение (Android, отдельный репозиторий `android/`) — сделан 2026-10-31 (1.14.0, `PROGRESS.md`)

Ветка от `homemusic` в своём worktree (`CLAUDE.md`, абзац про `android/`; правила — `android/CLAUDE.md`).
Зависит только от контракта этапа 1. Цель — те же данные из приложения, где сейчас слушает большинство пользователей.

1. **Место запуска волны.** `HubWave.start(activity, seed, fallback, origin)`; тело `{seed, origin, client}`,
   `client` = `app/<versionName>` (как `HubPlays.client`). Места: карточка «Моя волна» на «Главной»
   (`HomeMixCards.kt`, `HubWave.start(it, null)`) → `home_card`; `bindRow` — `track_menu` / `artist_menu` (меню,
   открытое из плеера, → `player`, если это различимо по месту вызова); «Радиостанции» (`startArtist`) → `radio`;
   `startArtistExact` — по месту вызова: `ArtistPageFragment` → `artist_page`, `ArtistBottomSheetDialog` →
   `artist_menu`; пересоздание после 404 (`WaveSession.startBody`) → `origin=restart`, `restart_of=<старый id>`.
2. **Плейлисты с id.** Нативный запуск плейлиста Navidrome пишет контекст `playlist:<id>` вместо `playlist`
   (найти все места: `PlaylistRowBottomSheetDialog`, страница плейлиста, карточки плейлистов `HomePlaylistCards`
   и др.). `NativeContexts.queue()` и логика «очередь наследует mix/wave» сохраняются. Хаб по `nd_playlist_id`
   относит прослушивание плейлиста «Микс дня» к миксу (§7.5).
3. **`launch_id` / `launch_origin`.** Новый id на каждый запуск очереди (`MediaManager.startQueue(..., context)` →
   `HubPlays.expectNativeQueue`), у элементов моста `playQueue` — взять пришедшие от PWA `launch_id/launch_origin`;
   волна — `wave:<sid>`. Поля в `PlayEvent` и в файле очереди событий (`PlayEvents.kt`): старый файл без полей
   читается. ▶ «Микса дня» на «Главной» (`HomeMixCards`) — `mix_card`; плейлист «Микс дня» — `nd_playlist`.
4. **Gating:** новые поля слать, только если `/healthz.capabilities` содержит `launch_events_v1` / `wave_origin_v1`
   — так же, как `HubClient.supportsTypedFavorites()` проверяет `typed_favorites_v1` (`HubClient.kt`).
5. **Тесты** (`testHomemusicDebugUnitTest`): тело старта волны для каждого места, `restart` с `restart_of`,
   контекст `playlist:<id>`, новый `launch_id` на каждую новую очередь и тот же — внутри очереди, сериализация
   событий со старым файлом, gating.
6. **Выкатка:** слияние в `homemusic`, release с `versionCode` больше, чем в `B:\Music\.apk\latest.json`, `notes`
   по-русски (например: «Статистика: приложение сообщает Хабу, откуда запущены волна и плейлисты»), проверка
   `curl http://localhost:8080/app/latest.json` (README, «Android-приложение»). `android/CLAUDE.md` и `PROGRESS.md`
   основного репозитория — запись.

---

## 11. Вопросы владельцу (если не ответил — действовать по умолчанию)

1. Кто видит аналитику? **По умолчанию** — все `is_admin` (сейчас это владелец).
2. Есть ли QA-аккаунт, который не надо смешивать с семьёй? **По умолчанию** — `analytics.exclude_users: []`
   в `config.yaml`; владелец впишет имя, если нужно.
3. Нужна ли на вкладке «Миксы» кнопка «Собрать заново этому пользователю» (существующий
   `POST /api/mixes/rebuild {user_id}`, ставит загрузки)? **По умолчанию — не делать** (экраны только читают).
4. Считать ли открытия страниц (сколько раз смотрел микс, но не запускал)? **По умолчанию — нет** (не просили; это
   новый вид сбора данных).

## 12. Подводные камни (чек-лист исполнителя)

- [ ] Ни одного сетевого запроса и ни одной записи в БД из админских GET (тест есть).
- [ ] Не использовать `taste.build_profile(model="v1")`, `taste_select.seeds()`, `build.cold_seeds`, `wave._rt`.
- [ ] `superseded_scrobble`, `ts > now`, клиенты `qa`/`*-debug`, импортные ♥, контексты `favorite:bootstrap/reconcile`
      — исключаются так же, как в профиле.
- [ ] Ключи треков (`nd:…`, `dz:…`, `file:…`) в адресах — только в query, закодированными.
- [ ] Дни и часы — в `Settings.timezone`; хранение — UTC naive, как везде в Хабе.
- [ ] `score_parts` не меняет прежние оценки; trace не меняет профиль; `launch_*` не меняют вкус.
- [ ] Старые миксы/сессии без новых полей показываются честно (`null`, «не записано», «приблизительно»),
      ничего не пересчитывается задним числом по текущему конфигу.
- [ ] Карта: подпись о доле объяснённой дисперсии; раскладка «Грани» — как честная альтернатива.
- [ ] PWA: 375 px без горизонтальной прокрутки страницы, обе темы, встроенный режим; новых npm-зависимостей нет.
- [ ] Реальные данные пользователей — не в логах, не в git, не в моках и тестах; копия БД для проверки — вне репозитория и
      удалена.
- [ ] Docker — только из PowerShell; после каждой сборки образа — `docker image prune -f`.
- [ ] `hub/API.md`, `MIXES.md`, `INSIGHTS.md`, `PROGRESS.md` обновлены в той же ветке, что и код.

## 13. Что сообщить владельцу в конце каждого этапа

Что влито и запушено (ветка, коммиты), какие тесты прошли (Хаб в Docker, embedder, PWA build, Android unit),
пересобран ли Хаб/embedder и какая версия приложения опубликована; что можно посмотреть в `/admin` и что пока
приблизительно (старая история без `launch_id`, восстановленные сессии волны).
