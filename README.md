# Anna — Telegram yordamchi (avtojavob kotiba + avtomatik bio)

Loyiha shaxsiy Telegram akkauntingizda (bot emas, **userbot** — Telethon orqali) ishlaydi:

1. **Avtojavob kotiba** — band bo'lganingizda shaxsiy yozganlarga AI (OpenAI) javob beradi:
   "Husan Suyunov hozirda band, xabaringizni yozib qoldiring, men unga yetkazaman". Xulosa alohida nazorat boti orqali sizga keladi.
2. **Avtomatik bio** — profil bio'si muntazam yangilanadi:

   ```
   Full Stack Developer | 🕒 14:35 | 🌧️ 18°C
   ```

Ma'lumotlar **MongoDB Atlas**da saqlanadi.

## Fayllar

| Fayl | Vazifa |
|---|---|
| `main.py` | Hammasini birga ishga tushiradi: kotiba + nazorat boti + bio |
| `bio_updater.py` | Avtomatik bio (alohida ham ishga tushirsa bo'ladi) |
| `tg_client.py` | Telegram login va maxfiy ma'lumotlarni yashiradigan logging |
| `userbot.py`, `control_bot.py`, `ai.py` | Avtojavob kotiba |
| `storage.py` | MongoDB Atlas bilan ishlash |
| `config.py` | `.env` dan sozlamalarni o'qish |

## 1. Kerakli narsalar

1. **Python 3.10+**
2. **API_ID va API_HASH** (shaxsiy akkaunt uchun):
   1. https://my.telegram.org ga telefon raqamingiz bilan kiring.
   2. **API development tools** bo'limini oching.
   3. *App title* va *Short name* ga istalgan nom yozing (masalan `anna`), *Platform* — `Desktop`.
   4. **Create application** → `App api_id` va `App api_hash` ni `.env` ga ko'chiring.
3. **MongoDB Atlas ulanish satri** — Atlas → *Database* → *Connect* → *Drivers*. Atlas → *Network Access* bo'limida
   kompyuteringiz/serveringiz IP manzili ruxsat etilgan bo'lishi kerak.
4. Faqat kotiba uchun: **Bot token** (@BotFather → `/newbot`, keyin botga **/start** bosing) va **OpenAI API key**.

## 2. O'rnatish

```bash
pip install -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env
```

`.env` faylini oching va qiymatlarni to'ldiring (`API_ID`, `API_HASH`, `MONGODB_URI`, ...).
`.env.example` ga **haqiqiy** qiymat yozmang — u git'ga qo'shiladi.

## 3. Avtomatik bio

### 3.1. Avval DRY_RUN bilan sinab ko'ring

`.env` da standart holatda `DRY_RUN=true`. Bu rejimda **Telegram'da hech narsa o'zgarmaydi**, faqat terminalda
yangi bio matni ko'rsatiladi:

```bash
python bio_updater.py --once     # bir marta tekshirib chiqadi
python bio_updater.py            # doimiy rejim (Ctrl+C bilan to'xtaydi)
```

Natija taxminan shunday bo'ladi:

```
[bio] INFO: Ob-havo (Toshkent, Uzbekistan): 🌤️ asosan ochiq, 7°C
[bio] INFO: [DRY_RUN] Telegram'da o'zgartirilmadi. Yangi bio bo'lardi: Full Stack Developer | 🕒 10:50 | 🌤️ 7°C (41/70)
```

Birinchi ishga tushirishda Telethon telefon raqamingizni (agar `.env` da `PHONE` bo'sh bo'lsa), Telegram'dan kelgan
kodni va (yoqilgan bo'lsa) ikki bosqichli parolni **terminalda** so'raydi. Ular hech qayerga yozilmaydi va logga
chiqmaydi. Shundan keyin `anna.session` fayli yaratiladi va qayta login kerak bo'lmaydi.

### 3.2. Haqiqiy ishga tushirish

Hammasi to'g'ri ekaniga ishonch hosil qilgach, `.env` da o'zingiz o'zgartiring:

```
DRY_RUN=false
```

va qayta ishga tushiring: `python bio_updater.py` (faqat bio) yoki `python main.py` (kotiba + bio).

### 3.3. Asl bio'ni yo'qotmaslik

> ⚠️ Dastur bio'ni **to'liq almashtiradi**. Hozirgi bio'dagi boshqa matn o'rniga
> `BIO_PREFIX | 🕒 vaqt | ob-havo` yoziladi. `BIO_PREFIX` (standart `Full Stack Developer`) doim saqlanadi.

Shuning uchun:
- Birinchi ishga tushishda (DRY_RUN rejimida ham) **hozirgi bio'ngiz zaxiralanadi**: mahalliy `bio_backup.json`
  fayliga va MongoDB'dagi `bio_state` kolleksiyasiga.
- Zaxira **faqat bir marta** yoziladi va keyin hech qachon ustidan yozilmaydi.
- Zaxira hech qayerga saqlanmasa, dastur bio'ni **o'zgartirmaydi**.
- Asl bio'ni ko'rish va qaytarish:

```bash
python bio_updater.py --show      # zaxiradagi asl bio
python bio_updater.py --restore   # asl bio'ni qaytaradi (DRY_RUN=false bo'lishi kerak)
```

### 3.4. Shahar va ko'rinishni o'zgartirish

Ob-havo **Open-Meteo**dan olinadi (API kalit kerak emas). Boshqa shahar uchun `.env` da:

```
CITY_NAME=Samarqand, Uzbekistan
LATITUDE=39.6542
LONGITUDE=66.9597
```

Koordinatalarni Google Maps'dan (joyni bosib turing) yoki https://open-meteo.com/en/docs dagi qidiruvdan olishingiz mumkin.
Soat har doim `TIMEZONE` (standart `Asia/Tashkent`) bo'yicha ko'rsatiladi, kompyuter vaqt zonasiga bog'liq emas.

| Sozlama | Standart | Izoh |
|---|---|---|
| `BIO_PREFIX` | `Full Stack Developer` | Doim saqlanadigan matn |
| `BIO_TEMPLATE` | `{prefix} \| 🕒 {time} \| {icon} {temp}°C` | Ko'rinish shabloni |
| `BIO_UPDATE_INTERVAL_SECONDS` | `300` | Bio yangilash oralig'i (min. 60) |
| `WEATHER_INTERVAL_SECONDS` | `900` | Ob-havo olish oralig'i (15 daqiqa) |
| `WEATHER_MAX_AGE_SECONDS` | `10800` | Eski ob-havo qancha vaqt ishlatiladi |
| `BIO_MAX_LENGTH` | `0` | 0 = avtomatik: oddiy akkaunt 70, Premium 140 |

**Iqtibos.** Bio oxiriga qisqa iqtibos qo'shiladi:

```
Full Stack Developer | 🕒 14:35 | 🌤️ 7°C | “Ilm — saodat kaliti”
```

- Iqtiboslar `quotes.txt` faylida turadi (har qatorda bittasi). Ular har kuni almashadi (Toshkent vaqti bilan).
- Bitta doimiy iqtibos kerak bo'lsa: `.env` da `BIO_QUOTE=Sizning iqtibosingiz`.
- O'chirish: `BIO_QUOTE_ENABLED=false`.
- Oddiy akkauntda bio 70 belgi, shuning uchun iqtibos ~22 belgidan oshmasin. Joy yetmasa sig'adigan iqtibos
  tanlanadi, hech biri sig'masa iqtibossiz chiqadi. Telegram Premium'da (140 belgi) uzunroq iqtibos ham sig'adi.

**Ob-havo belgilari** (WMO kodlari): ☀️ ochiq (0), 🌤️ asosan ochiq (1), ⛅ qisman bulutli (2), ☁️ bulutli (3),
🌫️ tuman (45, 48), 🌧️ shivalama/yomg'ir/jala (51–67, 80–82), ❄️ qor (71–77, 85, 86), ⛈️ momaqaldiroq (95–99).
Harorat butun songa yaxlitlanadi (°C).

### 3.5. Xatolar qanday boshqariladi

- **Ob-havo xizmati ishlamasa** — dastur to'xtamaydi. Oxirgi muvaffaqiyatli ob-havo (MongoDB'da ham saqlanadi,
  qayta ishga tushganda ham ishlatiladi) 3 soatgacha ko'rsatiladi. Undan eski bo'lsa `🌡️ --°C` chiqadi.
  Qayta urinish har `WEATHER_RETRY_SECONDS` da.
- **Telegram FloodWait** — Telegram aytgan vaqtcha kutib, keyin davom etadi.
- **Internet uzilishi / vaqtinchalik xatolar** — `BIO_RETRY_SECONDS` dan keyin qayta uriniladi.
- **Bio o'zgarmagan bo'lsa** Telegram'ga so'rov yuborilmaydi.
- **Bio juda uzun bo'lsa** — avval ob-havo, keyin vaqt qismi tashlab yuboriladi (`BIO_PREFIX` qoladi).
- **API_ID/API_HASH noto'g'ri** yoki **session bekor qilingan** bo'lsa — tushunarli xabar chiqadi va to'xtaydi.
- **MongoDB ishlamasa** — bio mahalliy `bio_backup.json` zaxirasi bilan ishlashda davom etadi.

**Nega standart 5 daqiqa?** Har daqiqada yangilash kuniga ~1440 ta so'rov degani va akkauntga FloodWait cheklovi
qo'yilishi mumkin. 300 soniyada soat 5 daqiqalik qadam bilan yangilanadi (14:30, 14:35, ...). Xohlasangiz
60–120 qilishingiz mumkin, lekin bu xavfliroq.

## 4. Avtojavob kotiba

```bash
python main.py
```

- Faqat shaxsiy chatlarga javob beradi (guruh, kanal, botlar e'tiborsiz).
- Ketma-ket yozilgan xabarlarga bitta javob beradi, "yozmoqda..." ko'rsatadi.
- **Siz o'zingiz chatga yozsangiz**, AI shu chatda 2 soat jim turadi (`OWNER_TAKEOVER_MINUTES`).
- Ovozli xabar, rasm, fayllar nazorat botiga yuboriladi.
- OpenAI ishlamay qolsa, bir marta tayyor shablon matn yuboriladi.
- `BIO_ENABLED=true` bo'lsa, avtomatik bio ham shu jarayonda ishlaydi.

| Buyruq | Vazifa |
|---|---|
| `/on`, `/off` | Avtojavobni yoqish / o'chirish |
| `/status` | Holat |
| `/inbox` | Javob kutayotganlar |
| `/reply <chat_id> <matn>` | Sizning nomingizdan javob |
| `/ignore <id/@username>` | Bu odamga avtojavob bermaslik (oila, yaqinlar) |
| `/unignore <id>`, `/ignored` | Istisnolarni boshqarish |

Xulosa xabariga **reply** qilib yozsangiz yoki "✍️ Javob berish" tugmasini bossangiz — matn o'sha odamga sizning nomingizdan ketadi.

## 5. Online holat

Dastur ishlab turganda akkauntingiz Telegram'da **"online"** ko'rinadi: har 60 soniyada online signali yuboriladi.
Dastur to'xtatilganda (Ctrl+C) akkaunt "offline" holatiga qaytariladi.

| Sozlama | Standart | Izoh |
|---|---|---|
| `KEEP_ONLINE` | `true` | `false` — o'chirish |
| `ONLINE_INTERVAL_SECONDS` | `60` | Signal oralig'i (min. 30) |

> ⚠️ Akkaunt online turganda Telegram **telefoningizga push-bildirishnoma yubormasligi mumkin**, chunki u sizni
> boshqa qurilmada "o'tiribdi" deb hisoblaydi. Muhim xabarlar baribir nazorat boti orqali keladi (bot
> bildirishnomalari alohida ishlaydi).

## 6. Ishga tushirish va to'xtatish

| Nima | Buyruq |
|---|---|
| Hammasi (kotiba + bio) | `python main.py` |
| Faqat bio | `python bio_updater.py` |
| Bio'ni bir marta sinash | `python bio_updater.py --once` |
| To'xtatish | Terminalda **Ctrl+C** |

To'xtatilganda bio oxirgi holatida qoladi. Asl holatga qaytarish: `python bio_updater.py --restore`.

> `main.py` va `bio_updater.py` ni **bir vaqtda** ishga tushirmang — ikkalasi bitta `anna.session` faylini
> ishlatadi. `main.py` ishlayotgan bo'lsa, bio allaqachon uning ichida yangilanmoqda.

## ⚠️ Xavfsizlik

- **`anna.session`** — bu fayl akkauntingizga parolsiz **to'liq kirish** beradi. Uni hech kimga bermang, chatga yoki
  bulutga yuklamang, git'ga qo'shmang (`.gitignore` da bor). Agar fayl begonaga tushib qolgan deb o'ylasangiz:
  Telegram → *Settings → Devices* → shu sessiyani tugating, faylni o'chiring va qayta login qiling.
- **`.env`** — API_HASH, MongoDB paroli, bot token va OpenAI kalitini saqlaydi. U `.gitignore` da. Parollar tasodifan
  chatga yoki git'ga tushib qolsa, ularni darhol almashtiring (MongoDB Atlas → *Database Access* → *Edit password*).
- Loglarda API_HASH, telefon raqami, token, MongoDB URI va tasdiqlash kodi yashiriladi (`***`).
- Papka OneDrive'da bo'lsa, session fayl ham bulutga sinxronlanadi — loyihani sinxronlanmaydigan papkaga
  ko'chirish yoki serverga qo'yish tavsiya etiladi.
- Kompyuter o'chsa dastur ham to'xtaydi. Doimiy ishlashi uchun VPS/serverga qo'ying.
