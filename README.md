# MYnyak Engsel Sunset - Bot Telegram by @JustZayy

CLI client untuk beberapa layanan provider Indonesia.

## Install & Run di Termux

1. Update & upgrade Termux:

```bash
pkg update && pkg upgrade -y
```

2. Install Git & Python:

```bash
pkg install git python -y
```

3. Clone repo:

```bash
git clone https://github.com/zaycrimson/telebot-dorxl.git
```

4. open directory
```bash
cd telebot-dorxl
```

5. create .env
```bash
nano .env
```
isi API XL dll kedalam .env


6. Setup:

```bash
bash setup.sh
```

7. Jalankan bot:

```bash
python bot_telegram.py
```

## .env Example
tambah ini dibawah env api yg udah kalian copy pemilik repo aslinya
```bash
TELEGRAM_BOT_TOKEN=BOT_TOKEN_KAMU
BOT_ALLOWED_IDS=ID_TELE_KAMU
CLI_COMMAND=python -u bot_telegram.py
WA_QRIS_FAMCODE=45c3a622-8c06-4bb1-8e56-bba1f3434600
CLI_READ_TIMEOUT=20.0
CLI_IDLE_TIMEOUT=2.0
CLI_PROMPT_GRACE=0.8
QRIS_OUTPUT_DIR=tmp_qris
QRIS_BOX_SIZE=10
QRIS_BORDER=4
WA_QRIS_PACKAGE_NUMBER=4
WA_QRIS_PAYMENT_NUMBER=3
WA_QRIS_PAYMENT_AMOUNT=5000
```

**Note:** Semua API/Token yang dimasukkan di .env harus didapat dari repository asli: https://github.com/purplemashu/me-cli-sunset agar bot berjalan dengan benar. Jangan sembarang masukin value.

## WA QRIS Button Flow

Tombol **⚡ WA QRIS** otomatis menjalankan flow CLI:  
`6` -> `WA_QRIS_FAMCODE` -> `WA_QRIS_PACKAGE_NUMBER` -> `WA_QRIS_PAYMENT_NUMBER` -> `WA_QRIS_PAYMENT_AMOUNT`.

## Credit

Script asli dari: https://github.com/purplemashu/me-cli-sunset

Bot Telegram dibuat oleh **@JustZayy**
