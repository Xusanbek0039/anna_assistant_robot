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
| `BIO_UPDATE_INTERVAL_SECONDS` | `60` | Bio va ismdagi soat yangilanish oralig'i (min. 60). 300 bo'lsa soat 4 daqiqagacha ortda qoladi |
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

**Ism yonida vaqt.** Familiyangiz yoniga joriy vaqt qo'shiladi: `Husan Suyunov 🕒 14:35`. U bio bilan **bitta
so'rovda** yangilanadi, shuning uchun Telegram'ga qo'shimcha yuk tushmaydi. Asl ism-familiya ham `bio_backup.json`
va MongoDB'ga saqlanadi, `--restore` ularni qaytaradi. O'chirish: `NAME_CLOCK_ENABLED=false`.
Ko'rinishni `NAME_TEMPLATE` orqali o'zgartirish mumkin (standart: `{last_name} 🕒 {time}`).

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
- O'zini "Husan Suyunov yaratgan sun'iy intellekt yordamchisi Anna" deb tanishtiradi. Husan haqida faqat
  `owner_profile.txt` dagi ochiq ma'lumotlarni (tug'ilgan sana/joy, saytlar) aytadi. Bu faylni o'zingiz tahrirlang.
- **Ovozli xabar**, audio va video-xabarlarni matnga o'giradi (`OPENAI_TRANSCRIBE_MODEL`, standart `whisper-1`),
  **rasmlarni** tushunadi (undagi matnni ham o'qiydi) va mazmuniga javob beradi.
- Ovozli xabar, rasm, fayllar nazorat botiga ham yuboriladi.
- OpenAI ishlamay qolsa, bir marta tayyor shablon matn yuboriladi.
- `BIO_ENABLED=true` bo'lsa, avtomatik bio ham shu jarayonda ishlaydi.

| Buyruq | Vazifa |
|---|---|
| `/auto` | **Aqlli rejim** — faqat siz band bo'lganda javob beradi (pastga qarang) |
| `/on`, `/off` | Doim javob berish / o'chirish |
| `/status` | Rejim va hozir nega javob beradi/bermaydi |
| `/tag <id/@username> <oila\|mijoz\|dost\|clear>` | Kontakt turi |
| `/tags` | Turlar ro'yxati |
| `/inbox` | Javob kutayotganlar |
| `/reply <chat_id> <matn>` | Sizning nomingizdan javob |
| `/ignore <id/@username>` | Bu odamga umuman tegmaslik (xabar ham kelmaydi) |
| `/unignore <id>`, `/ignored` | Istisnolarni boshqarish |

Xulosa xabariga **reply** qilib yozsangiz yoki "✍️ Javob berish" tugmasini bossangiz — matn o'sha odamga sizning nomingizdan ketadi.

### Aqlli rejim (`/auto`)

Kotiba quyidagi tartibda qaror qiladi:

1. **Google Calendar'da hozir uchrashuv bor** → javob beradi: "taxminan 15:00 dan keyin bo'shaydi".
   Uchrashuv nomi va tafsilotlari **hech qachon** aytilmaydi.
2. **Ish vaqti** (`BUSY_HOURS`, masalan `Du-Ju 09:00-18:00; Sh 10:00-14:00`) → javob beradi.
3. **Siz oxirgi 10 daqiqada biror joyga yozgansiz** (`OWNER_ACTIVE_MINUTES`) → jim turadi, o'zingiz javob berasiz.
4. Aks holda (telefon yonida emassiz) → javob beradi.

**Google Calendar ulash:** calendar.google.com → ⚙️ Sozlamalar → chapda kalendaringiz → *Integrate calendar* →
**"Secret address in iCal format"** (Maxfiy iCal manzili) ni nusxalab, `.env` ga `CALENDAR_ICS_URL=...` qilib qo'ying.
Bu havola **maxfiy** — u orqali kalendaringizni istalgan kishi o'qiy oladi. Faqat `.env` da saqlang (logda yashiriladi).
Butun kunlik tadbirlar va "Bo'sh" (*Free*) deb belgilanganlar bandlik hisoblanmaydi.

### Kontakt turlari (`/tag`)

| Tur | Kotiba nima qiladi |
|---|---|
| `oila` | **Javob bermaydi**, faqat sizga 👨‍👩‍👧 belgisi bilan xabar beradi |
| `mijoz` | Rasmiy ohang, botda 💼 belgisi |
| `dost` | Samimiy ohang |
| *(turi yo'q, kontaktlaringizda bor)* | Oddiy muloyim |
| *(turi yo'q, notanish)* | Ehtiyotkor. **Aniq spam** (reklama, "pul yutdingiz", shubhali havola) bo'lsa — javob ham, bildirishnoma ham yo'q |

### Ovozli javob

Ovozli xabar yoki video-xabarga kotiba **ovozli xabar** bilan javob beradi (`VOICE_REPLY_ENABLED`, ovoz: `TTS_VOICE`).
Ovoz yaratib bo'lmasa, matn yuboriladi. O'zbek tilida talaffuz biroz chet el aksentli bo'lishi mumkin —
yoqmasa `VOICE_REPLY_ENABLED=false`.

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

## 6. Botdan boshqarish, hisobot va nazorat

`.env` ni tahrirlash va dasturni qayta ishga tushirish shart emas — bu sozlamalar MongoDB'da saqlanadi
va qayta ishga tushganda ham saqlanib qoladi.

**Menyu:** nazorat botida pastdagi **/** yoki ☰ **Menyu** tugmasini bosing — barcha buyruqlar tavsifi bilan chiqadi.
Menyu faqat sizga ko'rinadi (begonalarga bo'sh). **`/status`** yoki **`/bio`** — tugmali boshqaruv paneli:
rejim (🤖 Aqlli / 🟢 Doim / ⚪️ O'chiq), bio yoqish/to'xtatish, iqtibos, online — hammasi bir bosishda.

| Buyruq | Vazifa |
|---|---|
| `/bio` | Joriy bio, iqtibos va online holati |
| `/bio on` / `/bio off` | Bio yangilashni yoqish / to'xtatish (bio oxirgi holatida qoladi) |
| `/bio now` | Bio'ni hozir yangilash |
| `/bio restore` | Asl bio va familiyani qaytarish (avtomatik yangilash to'xtaydi) |
| `/quote Ilm — kuch` | Doimiy iqtibos |
| `/quote auto` / `/quote off` | Har kuni almashadigan (`quotes.txt`) / iqtibossiz |
| `/online on` / `/online off` | Akkauntni online ko'rsatish |
| `/report` | Bugungi hisobot |
| `/usage` | Bugungi AI sarfi va limitlar |
| `/health` | MongoDB, OpenAI, Telegram holatini hozir tekshirish |

**Kunlik hisobot** har kuni `REPORT_TIME` (standart 21:00) da keladi:

```
📊 Bugungi hisobot — 12-noyabr
👥 Yozganlar: 12 kishi (34 ta xabar)
🔴 muhim: 3 | 📞 chaqirishgan: 1 | 🚫 spam: 2
🤖 AI: 41 / 300 so'rov (35 javob, 4 ovoz, 2 rasm), ~38.2k token
📬 Javob kutayotganlar: 3
🔴 Ali: Sayt buyurtma qilmoqchi, narxini so'radi
```

**Xarajat nazorati.** Har bir AI so'rovi (javob, ovozni matnga o'girish, rasm, ovozli javob) hisoblanadi.
`AI_DAILY_LIMIT` (kuniga jami, standart 300) yoki `AI_CHAT_DAILY_LIMIT` (bitta odamga, standart 30) tugasa,
kotiba AI'siz ishlaydi: shablon matn bir marta yuboriladi, xabarlar baribir sizga yetkaziladi. Limit tugagani
haqida botga kuniga bir marta xabar keladi.

**Holat nazorati.** Har 5 daqiqada MongoDB va Telegram, har 30 daqiqada OpenAI tekshiriladi. Biror qism
ishdan chiqsa — `❌ MongoDB ishlamayapti`, tiklansa — `✅ MongoDB qayta ishlayapti` (har tekshiruvda takrorlanmaydi).
Bio, online yoki hisobot qismi xato bilan to'xtasa ham botga xabar keladi, qolgan qismlar ishlashda davom etadi.

## 6.1. 💰 Shaxsiy buxgalter (kirim-chiqim hisobi)

Nazorat botiga oddiy matn yozing yoki **ovozli xabar** yuboring — Anna uni kirim yoki chiqim sifatida yozadi:

| Siz yozasiz / aytasiz | Natija |
|---|---|
| `bugun nonga 25 ming, zapravkaga 30 ming ishlatdim` | 🔻 non 25 000 (oziq-ovqat), 🔻 zapravka 30 000 (transport) |
| `Alidan 200 ming oldim` | 🟢 kirim 200 000 (Ali) |
| `oylik tushdi 6 mln` | 🟢 kirim 6 000 000 (oylik) |
| `kecha taksiga 18 ming` | 🔻 kechagi sana bilan yoziladi |
| `Validan 1 mln qarz oldim` / `Aliga 300 ming qarz berdim` | 📥/📤 qarz — kirim-chiqimga qo'shilmaydi, `/balance` da alohida |

Har yozuvdan keyin tasdiq keladi: **↩️ Bekor qilish** va **✏️ Kategoriya** (kategoriyani tuzatish) tugmalari bilan.
AI ishlamasa yoki kunlik AI limiti tugasa — oddiy qoidalar bilan taxminiy yoziladi (⚠️ belgisi bilan).
Xulosa xabariga *reply* qilsangiz yoki «✍️ Javob berish» bosilgan bo'lsa — matn avvalgidek o'sha odamga ketadi.

| Buyruq | Vazifa |
|---|---|
| `/fin` | Moliya paneli (Bugun / 3 kun / Hafta / Oy / Balans / Tarix / Excel / Byudjet) |
| `/today`, `/days3`, `/week`, `/month` | Hisobot: kirim, chiqim, balans, kategoriyalar (%), eng katta xarajat, kunlik o'rtacha, oldingi davr bilan solishtirish |
| `/balance` | Umumiy balans va ochiq qarzlar (kim kimga qancha) |
| `/history` | Oxirgi 15 yozuv, 🗑 bilan o'chirish |
| `/undo` | Oxirgi yozuvni bekor qilish |
| `/budget oziq-ovqat 2000000`, `/budget jami 8mln` | Oylik byudjet; 80% va 100% da ogohlantiradi. `/budget` — holati, `/budget kafe off` — o'chirish |
| `/export`, `/export 7`, `/export all` | Excel'da ochiladigan CSV fayl |

**Avtomatik hisobotlar** `FINANCE_REPORT_TIME` (standart 22:00) da: har kuni — kunlik, har 3 kunda — 3 kunlik,
`FINANCE_WEEK_DAY` kuni (standart yakshanba) — haftalik, oyning oxirgi kuni — oylik. Bugun hech narsa
yozilmagan bo'lsa — eslatma keladi. O'chirish: `FINANCE_ENABLED=false`.

## 7. Ishga tushirish va to'xtatish

| Nima | Buyruq |
|---|---|
| **Tavsiya: yiqilsa o'zi qayta ishga tushadi** | `run.bat` (ikki marta bosing) |
| Hammasi (kotiba + bio) | `python main.py` |
| Faqat bio | `python bio_updater.py` |
| Bio'ni bir marta sinash | `python bio_updater.py --once` |
| To'xtatish | Terminalda **Ctrl+C** |

To'xtatilganda bio oxirgi holatida qoladi. Asl holatga qaytarish: `python bio_updater.py --restore`.

## 8. Testlar

```bash
pip install -r requirements-dev.txt
python -m pytest -q tests
```

Testlar haqiqiy `.env`, Telegram, OpenAI va MongoDB'ga **tegmaydi** (soxta obyektlar ishlatiladi), 3 soniyada o'tadi.
Har bir o'zgarishdan keyin ishga tushiring — eski funksiyalar buzilmaganini ko'rsatadi.

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
