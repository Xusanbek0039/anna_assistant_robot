# Anna — Telegram avtojavob kotiba

Siz band bo'lganingizda Telegram'da **shaxsiy** yozganlarga sizning akkauntingizdan AI (OpenAI) javob beradi:
"Husan Suyunov hozirda band, xabaringizni yozib qoldiring, men unga yetkazaman" mazmunida suhbatlashadi,
xabar mazmunini aniqlab oladi. Xulosa alohida nazorat boti orqali sizga keladi.

## 1. Kerakli narsalar
1. **API_ID va API_HASH** — https://my.telegram.org → *API development tools* → ilova yarating.
2. **Bot token** — Telegram'da @BotFather → `/newbot`. Keyin o'sha botga kirib **/start** bosing.
3. **OpenAI API key** — https://platform.openai.com/api-keys
4. Python 3.10+

## 2. O'rnatish
```bash
pip install -r requirements.txt
cp .env.example .env      # Windows: copy .env.example .env
```
`.env` faylini to'ldiring.

## 3. Ishga tushirish va login
```bash
python main.py
```
Birinchi marta telefoningizga Telegram kod yuboradi — terminalga kiriting. Ikki bosqichli parol bo'lsa, uni ham so'raydi.
Shundan keyin `anna.session` fayli yaratiladi va qayta login kerak bo'lmaydi.

## 4. Qanday ishlaydi
- Faqat shaxsiy chatlarga javob beradi (guruh, kanal, botlar e'tiborsiz).
- Ketma-ket yozilgan xabarlarga bitta javob beradi, "yozmoqda..." ko'rsatadi.
- **Siz o'zingiz chatga yozsangiz**, AI shu chatda 2 soat jim turadi (`OWNER_TAKEOVER_MINUTES`).
- Ovozli xabar, rasm, fayllar nazorat botiga yuboriladi.
- OpenAI ishlamay qolsa, bir marta tayyor shablon matn yuboriladi.

## 5. Nazorat boti buyruqlari
| Buyruq | Vazifa |
|---|---|
| `/on`, `/off` | Avtojavobni yoqish / o'chirish |
| `/status` | Holat |
| `/inbox` | Javob kutayotganlar |
| `/reply <chat_id> <matn>` | Sizning nomingizdan javob |
| `/ignore <id/@username>` | Bu odamga avtojavob bermaslik (oila, yaqinlar) |
| `/unignore <id>`, `/ignored` | Istisnolarni boshqarish |

Xulosa xabariga **reply** qilib yozsangiz yoki "✍️ Javob berish" tugmasini bossangiz — matn o'sha odamga sizning nomingizdan ketadi.

## ⚠️ Xavfsizlik
- `.env` va `anna.session` fayllarini **hech kimga bermang** — session fayli akkauntingizga to'liq kirish beradi.
- Kompyuter o'chsa bot ham to'xtaydi. Doimiy ishlashi uchun VPS/serverga qo'ying.
- Userbot faqat kiruvchi xabarlarga javob beradi va rate-limit bor — spam qilmang, aks holda Telegram akkauntni cheklashi mumkin.
