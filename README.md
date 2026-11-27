# Anna — Telegram yordamchi (avtojavob kotiba + avtomatik bio)

Loyiha shaxsiy Telegram akkauntingizda (bot emas, **userbot** — Telethon orqali) ishlaydi:

1. **Avtojavob kotiba** — band bo'lganingizda shaxsiy yozganlarga AI (OpenAI) javob beradi:
   "Husan Suyunov hozirda band, xabaringizni yozib qoldiring, men unga yetkazaman". Xulosa alohida nazorat boti orqali sizga keladi.
   «📩 Yangi xabar» bildirishnomalarini o'chirish: botda `/yangixabar off` yoki bildirishnoma tagidagi
   «🔕 «Yangi xabar» kelmasin» tugmasi (🔴 SHOSHILINCH va 📞 chaqiruv baribir keladi).
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
| `tg_login.py` | 🔑 Telegram akkauntni nazorat boti orqali ulash (telefon → kod → 2FA parol), sessiya MongoDB'da; `/tglogin`, `/tglogout` |
| `userbot.py`, `control_bot.py`, `ai.py` | Avtojavob kotiba |
| `voicelab.py` | VoiceLab (voicelab.uz): ovozli xabarlarni matnga o'girish va o'zbekcha ayol ovozida javob (Madina) — ovozli javob faqat VoiceLab |
| `finder.py` | Chatlardan aqlli qidirish: «Ali yuborgan manzilni top», «wifi parolini qidir» — matn, username, raqam, havola, rasm, fayl, lokatsiya (ism aytilmasa — barcha chatlardan) |
| `archive.py` | Chat arxivi: barcha shaxsiy chatlar (istisnodagilar ham) saqlanadi; suhbatdosh xabar yoki butun chatni o'chirsa — bot xabar beradi va yozishmani **HTML fayl** qilib yuboradi (botda «🗂 Chat arxivi» tugmasi yoki /arxiv) |
| `gdrive.py`, `drive_auth.py` | Chat arxivini Google Drive papkasiga HTML qilib yuklash (har chatga bitta fayl, o'zgarganda yangilanadi). Bir martalik sozlash: `python drive_auth.py` — `.env.example` dagi Google bo'limiga qarang |
| `profiles.py` | Profil kuzatuvi: yozishgan odamlar ism, username, bio, rasmini o'zgartirsa yoki akkauntini o'chirsa — xabar; profil rasmlari tarixi Drive'ga |
| `watchers.py` | 🔔 Eslatmalar: «95 446 68 86 raqami tg ga qo'shilsa xabar ber», «@ali online bo'lsa xabar ber» — hodisa ro'y berganda bot xabar beradi; panelda «➕ Yangi eslatma» tugmasi va Telegram cheklovi holati ko'rinadi |
| `instagram.py` | 📸 Instagram (`/ig`): bir marta ko'riladigan rasm/videolarni ochmasdan saqlash, Direct'ga avtojavob, yozishmalar arxivi (eski tarix bilan) va Google Drive'ga HTML nusxasi, qaytarib olingan (unsend) xabarlar, chatni botda o'qish |
| `groups.py` | 👥 Guruhlar (`/guruhlar`): akkauntingizdagi guruhlar ro'yxati — yoqilgan guruhga kelgan xabarlar (matn, rasm, video, ovozli, fayl) botga nusxalanadi |
| `promises.py` | AI chat xulosasi (/xulosa) va va'dalar kuzatuvi (/vadalar): kechqurun yangi va'dalarni topadi, ertalab muddati kelganlarini eslatadi |
| `weekly.py` | Haftalik chat hisoboti: faol suhbatlar, kim o'chirdi/tahrirladi, kim javob kutyapti |
| `backup.py`, `restore_backup.py` | Bazani har kuni Drive'ga zaxiralash (`Anna zaxira/<sana>/`) va undan tiklash: `python restore_backup.py <papka> --yes` |
| `schedule.py` | Har kuni belgilangan vaqtda bajariladigan vazifalar uchun umumiy sikl |
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

Ovozli xabar yoki video-xabarga kotiba **ovozli xabar** bilan javob beradi (`VOICE_REPLY_ENABLED`).

- Ovoz faqat **VoiceLab** orqali (o'zbek ayol ovozi «Madina — Honey», `VOICELAB_TTS_VOICE`, `VOICELAB_API_KEY` kerak).
- Matn o'qishdan oldin tayyorlanadi: `25 000 so'm` → «yigirma besh ming soʻm», `15:00 dan` → «oʻn beshdan»,
  `14-noyabr` → «oʻn toʻrtinchi noyabr», `o'`/`g'` → `oʻ`/`gʻ`, emoji va havolalar olib tashlanadi,
  kirillcha o'zbek matni lotinga o'giriladi.
- VoiceLab ishlamasa (kalit, kredit, tarmoq...) — suhbatdoshga matn yuboriladi va boshqaruv boti sizga
  muammo sababini yozadi (bir xil xato 30 daqiqada bir martadan ko'p emas).
- **Bir nechta VoiceLab kaliti (soni cheklanmagan — 5, 6 ta ham bo'ladi):**
  `VOICELAB_API_KEY=kalit1,kalit2,kalit3,kalit4,kalit5`. Kalitlar **aylanma** ishlatiladi: 1 → 2 → 3 → … →
  oxirgisi → yana 1. Joriy kalit tugamaguncha o'sha ishlatiladi; kredit tugasa (yoki kalit yaroqsiz bo'lsa) so'rov
  darhol navbatdagisi bilan qayta yuboriladi va bot yozadi: `⚠️ VoiceLab 3-kalit (…abcd): kredit tugadi.
  1-kalit (…5SEw) ga o'tildi.` Yaqinda (6 soat ichida) tugagan kalit navbat oxiriga suriladi, lekin baribir
  sinab ko'riladi — kredit to'ldirilsa, bot o'zi unga qaytadi. Hammasi tugasa — bir marta ogohlantiradi.
  Joriy kalit bazada eslab qolinadi (kalitning o'zi emas), qayta ishga tushganda ham 1-kalitdan boshlamaydi.
  Hozir qaysi kalit ishlayotgani: `/health`.
- O'chirish: `VOICE_REPLY_ENABLED=false`.

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

## 6.2. 🔔 Eslatmalar (raqam Telegram'ga qo'shilsa / odam online bo'lsa)

Nazorat botiga oddiy yozing yoki **ovozli** ayting — hodisa haqiqatan ro'y berganda bot xabar beradi:

| Siz yozasiz / aytasiz | Nima bo'ladi |
|---|---|
| `95 446 68 86 raqami tg ga qo'shilsa / akkaunt ochsa / yashirindan chiqarsa menga xabar ber` | Har 15 daqiqada tekshiriladi. Raqam Telegram'da topilsa (yangi akkaunt ochsa yoki «Raqamim orqali kim topa oladi» maxfiyligini ochsa) — xabar: ismi, username, ID va «💬 Lichkaga o'tish» tugmasi |
| `@username online bo'lsa xabar ber`, `Vali onlayn bo'lsa ayt`, `id 123456789 online bo'lsa xabar ber` | U online bo'lganda **bir marta** xabar keladi |
| `Ali har safar online bo'lganda xabar ber` | **Har gal** online bo'lganda (orada kamida 10 daqiqa) |
| `95 446 68 86 raqami online bo'lsa xabar ber` | Raqam egasi topiladi va uning online holati kuzatiladi |
| Bildirishnomaga *reply*: `shu user online bo'lsa menga xabar ber` | O'sha odamga eslatma qo'yiladi (matn unga yuborilmaydi) |

- **🔔 Eslatmalar** tugmasi yoki `/eslatma` — ro'yxat va 🗑 bilan o'chirish. Tez qo'yish: `/eslatma @username`,
  `/eslatma 954466886` (raqam — Telegram'da paydo bo'lishi), `/eslatma Ali har safar`.
- Raqam Telegram'ning `contacts.resolvePhone` usuli bilan tekshiriladi — **kontaktlaringizga qo'shilmaydi**,
  u odamga hech narsa ko'rinmaydi. O'zbekiston raqamini 9 xonali (`95 446 68 86`) yoki `+998` bilan yozing;
  boshqa davlat raqami `+` bilan to'liq yoziladi.
- Online holat real vaqtda (Telegram yuboradigan yangilanish) va zaxira sifatida har 60 soniyada tekshiriladi.
  Agar odam «So'nggi faollik»ni sizdan yashirgan bo'lsa (yoki siz o'zingiznikini yashirgan bo'lsangiz), Telegram
  online holatini ko'rsatmaydi — eslatma qo'yilayotganda bot buni aytadi.
- Odam eslatma qo'yilgan paytda allaqachon online bo'lsa — keyingi safar online bo'lganda xabar keladi.
- **➕ Yangi eslatma** tugmasi: bosib, raqam yoki `@username` yozsangiz — eslatma qo'yiladi.
- Botga **faqat raqam** yoki **faqat `@username`** yozsangiz ham bot «Buni eslatma deb tushundim, qo'yaymi?»
  deb so'raydi (oldin bunday matn «Kimga javob yozmoqchisiz?» degan foydasiz javob olardi).
- Telegram raqam tekshiruvini (`contacts.resolvePhone`) qattiq cheklaydi. Cheklov kelsa, ro'yxatda «Telegram raqam tekshiruvini vaqtincha cheklab qo'ydi — N soatdan keyin davom etadi» deb ko'rsatiladi. Bu dasturdagi xato emas: kutish vaqti bazada saqlanadi, shuning uchun qayta ishga tushganda ham cheklov qaytadan boshlanmaydi.
- Eslatmalar MongoDB'da saqlanadi — qayta ishga tushganda ham davom etadi.

| Sozlama | Standart | Izoh |
|---|---|---|
| `WATCH_ENABLED` | `true` | Eslatmalarni o'chirish: `false` |
| `WATCH_PHONE_MINUTES` | `15` | Raqam tekshiruvi oralig'i (min. 5) |
| `WATCH_ONLINE_SECONDS` | `60` | Online holat zaxira tekshiruvi (min. 30) |
| `WATCH_ONLINE_COOLDOWN_MINUTES` | `10` | «Har safar» rejimida xabarlar orasidagi eng kam vaqt |
| `WATCH_COUNTRY_CODE` | `998` | 9 xonali raqamga qo'shiladigan kod |

## 6.3. 📸 Instagram (bir martalik media, avtojavob, chat arxivi)

Instagram akkauntingiz nazorat botiga ulanadi va botdan boshqariladi: **📸 Instagram** tugmasi yoki `/ig`.

**Ulash** (bir marta):

1. `pip install -r requirements.txt` — `instagrapi` shu yerda (serverda: qayta deploy qiling).
   Kutubxona o'rnatilmasa, `/ig` paneli shuni aytadi va **qolgan hamma narsa normal ishlaydi**.
2. Botga yozing: `/iglogin username parol` — xabaringiz **darhol o'chiriladi**, parol hech qayerga yozilmaydi.
   Instagram SMS/email kod yoki ikki bosqichli himoya kodini so'rasa — bot so'raydi, kodni botga yozasiz
   (bekor qilish: `bekor`). Instagram «ilova eski» desa, bot bir nechta Instagram ilova versiyasini o'zi
   sinab ko'radi.
3. Bu usul ishlamasa (Instagram «shubhali kirish» desa): kompyuterda instagram.com ga kiring →
   F12 → *Application* → *Cookies* → `sessionid` qiymatini nusxalang → botga `/igsession QIYMAT`.
   Bu yo'l login bosqichini chetlab o'tadi, shuning uchun serverda ishonchliroq.

Faqat sessiya MongoDB'da saqlanadi — dastur qayta ishga tushganda login so'ralmaydi. Chiqish: `/iglogout`.

> **«Your version of Instagram is out of date» / `ClientNeedsUpgradeError`** — bu `instagrapi` ning ma'lum
> xatosi (GitHub #2971, #2881): Instagram aslida boshqa narsani so'ragan bo'ladi (odatda server IP'sidan
> kirishni tasdiqlash, email kod yoki noto'g'ri parol), kutubxona esa uni «ilova eski» deb ko'rsatadi.
> Ilova versiyasini almashtirish yordam bermaydi. Yechim: telefonda Instagram'ni ochib «Ha, men edim» ni
> bosing va qayta urining — yoki **`/igsession`** usulini ishlating (u login bosqichini chetlab o'tadi).
> Server (Render)da ishlasa, darhol `/igsession` bilan boshlash tavsiya etiladi.

| Paneldagi tugma | Nima qiladi |
|---|---|
| 🔥 Bir martalik media | Kimdir Direct'da bir marta ko'riladigan (yoki «qayta ko'rish mumkin») rasm/video yuborsa — dastur uni **ochmasdan** yuklab oladi va botga oddiy, saqlasa bo'ladigan holda yuboradi. Instagram'da media «ochilmagan» bo'lib qoladi, yuboruvchiga hech narsa bildirilmaydi |
| 🤖 Avtojavob | Anna Direct'dagi shaxsiy yozishmalarga ham javob beradi. Telegram rejimiga (`/auto`, `/on`, `/off`) bo'ysunadi; Instagram'da o'zingiz yozsangiz — o'sha chatda 120 daqiqa jim turadi. Guruhlar va «So'rovlar» papkasiga javob bermaydi |
| 🗂 Arxiv | Har bir yozishma **avtomatik** arxivlanadi (MongoDB). Kimdir xabarini **qaytarib olsa** (unsend) — botga matni bilan xabar keladi va arxivda qizil belgi bilan qoladi |
| 📥 Xabarlar botga | Direct'ga kelgan har bir yangi xabar botga keladi |
| 🖼 Rasm/video/ovozni saqlash | Yozishmalardagi **rasm, video, ovozli xabar va GIF** arxivga yuklab olinadi. Suhbatdosh keyin o'chirsa ham sizda qoladi. Bir martalik media ham shu yerga tushadi |
| ☁️ Drive'ga saqlash | Har bir chat Google Drive'dagi **`Instagram/`** papkasiga HTML fayl bo'lib yuklanadi va o'zgargan sayin yangilanib boradi. Katta fayllar asl holida `Instagram/<chat>/` papkasiga ketadi |

Instagram bildirishnomasiga **reply** qilib yozsangiz (yoki «✍️ Javob berish») — javob Instagram'ga sizning nomingizdan ketadi.

### 📥 Havoladan yuklash (post, reels, storis)

Instagram havolasini botga **shunchaki tashlang** — media yuklab beradi. Boshqa bot kerak emas:

| Havola | Natija |
|---|---|
| `instagram.com/p/XXXX/` | Post rasmi yoki videosi; **albom** bo'lsa hammasi ketma-ket |
| `instagram.com/reel/XXXX/` | Reels videosi (davomiyligi bilan) |
| `instagram.com/tv/XXXX/` | IGTV videosi |
| `instagram.com/stories/user/123456/` | Storis rasmi yoki videosi |

Buyruq bilan ham bo'ladi: `/igyukla <havola>`. Bir xabarda 5 tagacha havola, albomdan 10 tagacha fayl.
Fayl nomida post kodi qoladi, izohda muallif (`@username`) va post matni keladi.

> **Yopiq akkauntdan** olish uchun ulangan Instagram akkauntingiz unga obuna bo'lishi kerak — bot sizning
> akkauntingiz ko'radigan narsani ko'radi, undan ortig'ini emas.

### HTML arxiv — Instagram ko'rinishida

**📄 HTML fayl** tugmasi yozishmani **Instagram uslubidagi** bitta faylga yig'adi (brauzerda ochiladi,
internet kerak emas):

- **rasm** ko'rinadi, **video** va **ovozli xabar** shu faylning o'zida ochilib eshitiladi/ko'riladi;
- 🔥 bir martalik media, 🗑 qaytarib olingan xabar, 🤖 Anna javobi — belgilangan holda;
- fayl yuklab olinmagan bo'lsa, sababi yoziladi («havolasi eskirgan» — Instagram uni endi bermaydi);
- katta fayllar HTML'ga sig'masa, ularning **Google Drive havolasi** qo'yiladi.

Fayllar bazada saqlanadi (`IG_MEDIA_MAX_KB` gacha) va kattalari Drive'ga. Yuklab olish holati `/ig`
panelida ko'rinadi: «🖼 Fayllar: 128 ta saqlandi (84 MB), 3 ta yuklanmoqda».

> Instagram media havolalari **tez eskiradi**, shuning uchun fayllar xabar kelganidan keyin darhol
> (20 soniya ichida) yuklab olinadi. Dastur o'chiq bo'lgan vaqtdagi media esa olinmay qolishi mumkin —
> bunda arxivda «havolasi eskirgan» deb belgilanadi.

### Yozishmalarni botda o'qish

**💬 Chatlar arxivi** tugmasi → chatlar ro'yxati (har birida xabarlar soni) → chatni bossangiz, yozishma
**bot ichida** ochiladi: yangi xabarlardan boshlab, 15 tadan, «◀️ Yangiroq / ▶️ Eskiroq» tugmalari bilan.
Belgilar: 🫵 — sizning xabaringiz, 🔥 — bir martalik media, 🗑 — qaytarib olingan, 🤖 — Anna javobi.
Xohlasangiz, **📄 HTML fayl** tugmasi butun yozishmani Telegram ko'rinishidagi fayl qilib yuboradi.

### Eski yozishmalar

Instagram ulangandan ~1 daqiqa keyin dastur **mavjud chatlarning eski yozishmalarini** ham arxivga oladi
(har chatdan `IG_SYNC_LIMIT` ta oxirgi xabar, standart 1000) — rasm va videolari bilan. Bu bir martalik
ish — tugagach har bir yangi xabar avtomatik arxivlanib boradi. Jarayon panelda ko'rinadi: «⏳ Eski yozishmalar olinmoqda — yana N ta chat».
Keyin yangi chat paydo bo'lsa, uning tarixi ham o'zi olinadi (har soatda tekshiriladi).

Bilish kerak:

- Bir martalik media faqat siz uni Instagram'da **ochguningizcha** olinadi — ochilgach Instagram uni hech kimga
  bermaydi. Bildirishnoma kelsa avval botni tekshiring; dastur ishlab turgan bo'lishi kerak. Ochib qo'yilganini
  bot «saqlab bo'lmadi» deb aytadi.
- Uchidan-uchiga shifrlangan (end-to-end) Instagram chatlarini Instagram serveri ham o'qiy olmaydi — ular ko'rinmaydi.
- Bu Instagram'ning **norasmiy** yo'li (rasmiy API shaxsiy Direct'ni bermaydi). Juda tez tekshirish akkauntga
  vaqtinchalik cheklov olib kelishi mumkin — Instagram «biroz kuting» desa, dastur 15 daqiqa o'zi to'xtab turadi.
  Tarix olinayotganda so'rovlar orasida 2.5 soniya pauza bor, shuning uchun u sekin ishlaydi.
- Arxivni o'qish so'rovlari chatni **«o'qildi» deb belgilamaydi** va bir martalik mediani ochmaydi.

| Sozlama | Standart | Izoh |
|---|---|---|
| `INSTAGRAM_ENABLED` | `true` | Instagram qismini butunlay o'chirish: `false` |
| `IG_POLL_SECONDS` | `30` | Direct shuncha soniyada bir tekshiriladi (min. 15) |
| `IG_AUTOREPLY` | `false` | Avtojavobning boshlang'ich holati (keyin botdan) |
| `IG_FORWARD` | `false` | «Xabarlar botga»ning boshlang'ich holati |
| `IG_SYNC_LIMIT` | `1000` | Eski yozishmalardan har chatdan nechta xabar olinadi (`0` — olmaslik) |
| `IG_DRIVE` | `true` | Instagram arxivi Google Drive'ga yuklanadi (oralig'i — `DRIVE_SYNC_MINUTES`) |
| `IG_MEDIA_ENABLED` | `true` | Rasm, video, ovozli xabarlarni arxivga yuklab olish |
| `IG_MEDIA_MAX_KB` | `3072` | Shu hajmgacha fayl bazaga saqlanadi va HTML ichida ochiladi |
| `IG_MEDIA_DRIVE_MAX_MB` | `100` | Drive'ga shu hajmgacha asl fayl yuklanadi |
| `IG_HTML_MEDIA_MB` | `40` | Bitta HTML faylga joylanadigan media hajmi |
| `IG_PROXY` | — | Faqat sotib olingan **proksi** ma'lumoti (Instagram paroli emas): `http://login:parol@ip:port` |

## 6.4. 👥 Guruhlar — xabarlarni klonlash

Botda **👥 Guruhlar** tugmasi (yoki `/guruhlar`) — akkauntingiz a'zo bo'lgan guruhlar ro'yxati chiqadi (kanallar
emas). Guruhni bosing — **✅** bo'ladi va shu guruhga kelgan har bir yangi xabar botga nusxalanib keladi:

- guruh nomi, kim yozgani (ism, @username), qaysi xabarga javob ekani;
- matn, rasm, video, ovozli xabar, fayl — asl ko'rinishida (`MAX_MEDIA_MB` dan kattasi — faqat izoh);
- **🔗 Guruhda ochish** tugmasi (ochiq guruh va superguruhlarda).

Yana bosilsa — o'chadi. Tanlov bazada saqlanadi. Yoqilganlar ro'yxat tepasida turadi; yangi qo'shilgan guruh
ko'rinmasa — **🔄 Yangilash**. Nusxaga reply qilib yozsangiz, javob guruhga **ketmaydi**.

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

## 9. Serverga qo'yish (Render)

| Sozlama | Qiymat |
|---|---|
| Servis turi | **Background Worker** (Web Service emas — bot HTTP port ochmaydi) |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `python main.py` |

Muhim nuqtalar:

- **Telegram akkaunt bot orqali ulanadi** — serverda terminal yo'q, shuning uchun sessiya topilmasa Anna nazorat
  botini ishga tushiradi va **botda** so'raydi: telefon raqam → Telegram kodi → (yoqilgan bo'lsa) 2FA parol.
  - Kodni **bo'sh joy bilan** yuboring: `1 2 3 4 5` — bo'sh joysiz yozilsa, Telegram kodni darhol bekor qiladi.
  - Kod va parolli xabarlar botda darhol o'chiriladi. Kirilgach sessiya MongoDB'da saqlanadi — keyingi
    ishga tushishlarda so'ralmaydi.
  - Login faqat akkaunt egasiga ruxsat: `OWNER_ID` (.env) yoki bazadagi oxirgi egasi. Birinchi marta (ikkalasi ham
    bo'lmasa) logda `/tglogin 123456` ko'rinishidagi bir martalik kod chiqadi — shuni botga yuborasiz.
  - `/tglogin` — qaysi akkaunt ulanganini ko'rsatadi; `/tglogout` — akkauntdan chiqadi (boshqa akkaunt ulash
    uchun): Anna qayta ishga tushadi va yangi login so'raydi.
  - Xohlasangiz eski usul ham ishlaydi: kompyuterda `python export_session.py` → chiqqan matn → `SESSION_STRING`.
- **MongoDB Atlas → Network Access** da `0.0.0.0/0` ruxsat etilgan bo'lsin (Render IP'si o'zgarib turadi).
- Environment o'zgaruvchisini saqlaganingizda Render servisni **qayta ishga tushiradi** — logda ketma-ket
  `Instance restarted` ko'rinsa, odatda sababi shu (xato emas).
- Logda ishga tushish bosqichlari va **xotira sarfi** ko'rinadi (`1/6` … `6/6`). Bot jim bo'lib qolsa,
  **oxirgi raqam** qayerda to'xtaganini aytadi: `2/6` — MongoDB, `3/6` — boshqa nusxa ishlab turibdi,
  `4/6` — shaxsiy akkaunt, `5/6` — nazorat boti (`BOT_TOKEN`).
- Logda xato ko'rinmasdan `Instance restarted` takrorlansa va xotira 400 MB dan oshib borsa — server
  xotira chegarasiga yetgan (bepul tarifda 512 MB). Yengillashtirish: `IG_SYNC_LIMIT` va
  `ARCHIVE_SYNC_LIMIT` ni kamaytirish yoki to'lovli tarif.
- Nazorat botiga ulanish 60 soniyada o'tmasa, bot sessiya faylini (`anna_bot.session`) o'zi tozalab bir
  marta qayta urinadi — eskirgan sessiya sabab jim qotib qolmaydi.
- Bepul tarifda servis uzoq jim turganda uxlab qolishi mumkin — o'sha vaqtda kelgan **bir martalik media
  saqlanmaydi**. Doimiy ishlashi uchun to'lovli tarif kerak.

## ⚠️ Xavfsizlik

- **`anna.session`** — bu fayl akkauntingizga parolsiz **to'liq kirish** beradi. Uni hech kimga bermang, chatga yoki
  bulutga yuklamang, git'ga qo'shmang (`.gitignore` da bor). Agar fayl begonaga tushib qolgan deb o'ylasangiz:
  Telegram → *Settings → Devices* → shu sessiyani tugating, faylni o'chiring va qayta login qiling.
- **`.env`** — API_HASH, MongoDB paroli, bot token va OpenAI kalitini saqlaydi. U `.gitignore` da. Parollar tasodifan
  chatga yoki git'ga tushib qolsa, ularni darhol almashtiring (MongoDB Atlas → *Database Access* → *Edit password*).
- Loglarda API_HASH, telefon raqami, token, MongoDB URI va tasdiqlash kodi yashiriladi (`***`).
- **Instagram sessiyasi** MongoDB'dagi `instagram` kolleksiyasida — u ham akkauntga parolsiz kirish beradi. Shuning
  uchun u Drive zaxirasiga qo'shilmaydi. Shubha bo'lsa: `/iglogout` va Instagram → *Sozlamalar → Hisoblar
  markazi → Parol va xavfsizlik → Qayerdan kirilgan* dan sessiyani tugating.
- Papka OneDrive'da bo'lsa, session fayl ham bulutga sinxronlanadi — loyihani sinxronlanmaydigan papkaga
  ko'chirish yoki serverga qo'yish tavsiya etiladi.
- Kompyuter o'chsa dastur ham to'xtaydi. Doimiy ishlashi uchun VPS/serverga qo'ying.
