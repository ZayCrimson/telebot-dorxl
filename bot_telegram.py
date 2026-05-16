import asyncio
import html
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# dotenv harus sebelum import app, biar env kebaca duluan.
load_dotenv()

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.client.engsel import get_balance, get_tiering_info
from app.service.auth import AuthInstance

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_IDS = {
    int(x.strip())
    for x in os.getenv("BOT_ALLOWED_IDS", "").split(",")
    if x.strip().isdigit()
}

BASE_DIR = Path(__file__).resolve().parent
CLI_COMMAND = os.getenv("CLI_COMMAND", f"{sys.executable} -u main.py")
READ_TIMEOUT = float(os.getenv("CLI_READ_TIMEOUT", "20.0"))
CLI_IDLE_TIMEOUT = float(os.getenv("CLI_IDLE_TIMEOUT", "2.0"))
CLI_PROMPT_GRACE = float(os.getenv("CLI_PROMPT_GRACE", "0.8"))
MAX_MESSAGE_LENGTH = 3900

MENU_BUTTONS = [
    [("⚡ WA QRIS", "wa_qris"), ("💳 WA DANA", "wa_dana")],
    [("📦 Paketku", "2"), ("✏️ Nama", "nama"), ("🔖 Bookmarks", "00")],
    [("🔥 HOT 1", "3"), ("🔥 HOT 2", "4"), ("🏪 Store Menu", "13")],
    [("🛒 Beli (Code)", "5"), ("🔍 FamCode", "6")],
    [("👥 Akrab", "9"), ("⭕ Circle", "10"), ("🔔 Notif", "n")],
    [("🕵️ Validasi", "v"), ("📝 Register", "r")],
    [("📜 Riwayat", "8"), ("🚪 Ganti Akun", "1")],
    [("📊 Segments", "11"), ("👨‍👩‍👧 Fam List", "12")],
    [("🔁 Loop FamCode", "7"), ("🎁 Redeem", "14")],
    [("⬅️ kembali", "00"), ("♻️ Restart CLI", "restart")],
]

# Alias tombol yang tidak ada padanan menu angka langsung.
# Ubah value-nya kalau menu CLI di repo kamu beda.
MENU_ALIASES = {
    "nama": "1",
}

WA_QRIS_FAMCODE = os.getenv("WA_QRIS_FAMCODE", "45c3a622-8c06-4bb1-8e56-bba1f3434600")
WA_QRIS_PACKAGE_NUMBER = os.getenv("WA_QRIS_PACKAGE_NUMBER", "4")
WA_QRIS_PAYMENT_NUMBER = os.getenv("WA_QRIS_PAYMENT_NUMBER", "3")
WA_QRIS_PAYMENT_AMOUNT = os.getenv("WA_QRIS_PAYMENT_AMOUNT", "5000")

WA_DANA_FAMCODE = os.getenv("WA_DANA_FAMCODE", WA_QRIS_FAMCODE)
WA_DANA_PACKAGE_NUMBER = os.getenv("WA_DANA_PACKAGE_NUMBER", "4")
WA_DANA_PAYMENT_NUMBER = os.getenv("WA_DANA_PAYMENT_NUMBER", "2")
WA_DANA_EWALLET_NUMBER = os.getenv("WA_DANA_EWALLET_NUMBER", "1")
DANA_NUMBER = os.getenv("DANA_NUMBER", os.getenv("WA_DANA_MSISDN", "081358238538"))
WA_DANA_MSISDN = DANA_NUMBER
WA_DANA_PAYMENT_AMOUNT = os.getenv("WA_DANA_PAYMENT_AMOUNT", "5000")


PROMPT_HINTS = (
    "Pilih menu:",
    "Pilih paket",
    "Pilih paket (nomor):",
    "Pilihan:",
    "Pilih opsi:",
    "Payment",
    "E-Wallet",
    "QRIS",
    "Beli dengan",
    "Enter option code",
    "Enter family code",
    "Start purchasing",
    "Use decoy package",
    "Pause on each",
    "Delay seconds",
    "Is enterprise",
    "Enter msisdn",
    "Enter NIK",
    "Enter KK",
    "validate",
    "y/n",
    "99",
)

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
QRIS_IMAGE_RE = re.compile(r"(?m)^QRIS_IMAGE_PATH:(.+)$")
DANA_PAYMENT_LINK_RE = re.compile(r"https?://[^\s<>\"]*dana\.id/[^\s<>\"]+")


def clean_output(text: str) -> str:
    text = ANSI_RE.sub("", text)
    text = text.replace("\r", "")
    # Bersihin spam terminal / banner ASCII kalau masih muncul dari output lama.
    text = re.sub(r"(?s)Clearing screen\.\.\..*?Detail Paket", "Detail Paket", text)
    text = re.sub(r"(?m)^\s*[\/\\_:.~|()]+\s*$", "", text)
    text = re.sub(r"\n{4,}", "\n\n", text)
    return text.strip()


def extract_qris_image_paths(text: str) -> tuple[str, list[Path]]:
    paths: list[Path] = []

    def _collect(match: re.Match) -> str:
        raw_path = match.group(1).strip()
        if raw_path:
            paths.append(Path(raw_path))
        return ""

    cleaned = QRIS_IMAGE_RE.sub(_collect, text)
    return cleaned.strip(), paths

def extract_dana_payment_links(text: str) -> tuple[str, list[str]]:
    """Pisahkan link DANA dari output CLI supaya dikirim sebagai pesan sendiri.

    Kalau link ikut masuk <pre>, Telegram jadi nyebelin buat dicopy.
    Jadi kita cabut link-nya dari teks panjang, lalu kirim link mentah satu per satu.
    """
    links: list[str] = []

    def _collect(match: re.Match) -> str:
        link = match.group(0).strip().rstrip(".,)")
        if link not in links:
            links.append(link)
        return "[LINK DANA DIKIRIM TERPISAH]"

    cleaned = DANA_PAYMENT_LINK_RE.sub(_collect, text)
    cleaned = re.sub(r"\n{4,}", "\n\n", cleaned)
    return cleaned.strip(), links


def chunk_text(text: str, limit: int = MAX_MESSAGE_LENGTH):
    if not text:
        return []
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut == -1 or cut < limit // 2:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip()
    chunks.append(text)
    return chunks


def is_allowed(user_id: int) -> bool:
    # Kalau BOT_ALLOWED_IDS kosong, bot tetap bisa jalan. Tapi sebaiknya isi whitelist.
    return not ALLOWED_IDS or user_id in ALLOWED_IDS


def build_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(text, callback_data=f"menu:{value}") for text, value in row] for row in MENU_BUTTONS]
    )


def format_profile() -> str:
    active_user = AuthInstance.get_active_user()
    if not active_user:
        return "⚠️ Belum ada user yang login. Tekan <b>1. Login/Ganti Akun</b> untuk login/pilih akun."

    try:
        balance = get_balance(AuthInstance.api_key, active_user["tokens"]["id_token"])
        balance_remaining = balance.get("remaining", 0)
        balance_expired_at = balance.get("expired_at", 0)
        expired_at_dt = datetime.fromtimestamp(balance_expired_at).strftime("%Y-%m-%d") if balance_expired_at else "N/A"

        point_info = "Points: N/A | Tier: N/A"
        if active_user.get("subscription_type") == "PREPAID":
            tiering_data = get_tiering_info(AuthInstance.api_key, active_user["tokens"])
            tier = tiering_data.get("tier", 0)
            current_point = tiering_data.get("current_point", 0)
            point_info = f"Points: {current_point} | Tier: {tier}"

        return (
            "🤖 <b>ZAY BOT TELE XL</b>\n\n"
            f"📱 <b>Akun:</b> <code>{html.escape(str(active_user.get('number', 'N/A')))}</code>\n"
            f"👤 <b>Tipe:</b> {html.escape(str(active_user.get('subscription_type', 'N/A')))}\n"
            f"💰 <b>Pulsa:</b> Rp {balance_remaining:,}\n"
            f"📅 <b>Aktif:</b> {expired_at_dt}\n"
            f"⭐ <b>{html.escape(point_info)}</b>"
        )
    except Exception as exc:
        logger.exception("Gagal ambil profil")
        return (
            "🤖 <b>ZAY BOT TELE XL</b>\n\n"
            f"⚠️ Error ambil data akun: <code>{html.escape(str(exc))}</code>\n"
            "Menu tetap bisa dipakai, tapi kalau token/login bermasalah ya botnya bukan dukun."
        )


@dataclass
class CliSession:
    user_id: int
    process: Optional[asyncio.subprocess.Process] = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def start(self) -> str:
        await self.stop()
        self.process = await asyncio.create_subprocess_shell(
            CLI_COMMAND,
            cwd=str(BASE_DIR),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        return await self.read_available(timeout=2.0)

    async def stop(self):
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=2)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        self.process = None

    async def ensure_started(self) -> str:
        if not self.process or self.process.returncode is not None:
            return await self.start()
        return ""

    async def send(self, text: str) -> str:
        async with self.lock:
            boot_output = await self.ensure_started()
            if not self.process or not self.process.stdin:
                return "CLI gagal dimulai. Cek command dan dependency."
            self.process.stdin.write((text + "\n").encode())
            await self.process.stdin.drain()
            # Kasih CLI waktu mulai render output. Tanpa ini, bot bisa baca terlalu cepat
            # saat proses fetching masih jalan dan Telegram keburu ngirim output separuh.
            await asyncio.sleep(0.5)
            output = await self.read_available(timeout=READ_TIMEOUT)
            return clean_output((boot_output + "\n" + output).strip())

    async def read_available(self, timeout: float = READ_TIMEOUT) -> str:
        """Baca output CLI sampai benar-benar idle, bukan cuma sampai jeda kecil.

        Versi lama nurunin timeout ke 0.15s setelah dapat output. Kalau CLI lagi
        fetching data dari API lalu baru render menu berikutnya, Telegram keburu
        mengirim output yang belum lengkap. Fungsi ini menunggu stdout idle dulu
        supaya menu/prompt yang muncul belakangan tetap ikut terkirim.
        """
        if not self.process or not self.process.stdout:
            return ""

        parts = []
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        last_output_at = started_at

        while True:
            now = loop.time()

            # Hard timeout total supaya bot tidak menggantung selamanya kalau CLI macet.
            if now - started_at >= timeout:
                break

            wait_time = min(1.2, max(0.1, timeout - (now - started_at)))
            try:
                data = await asyncio.wait_for(self.process.stdout.read(4096), timeout=wait_time)
            except asyncio.TimeoutError:
                # Selesai hanya kalau stdout sudah idle cukup lama.
                if loop.time() - last_output_at >= CLI_IDLE_TIMEOUT:
                    break
                continue

            if not data:
                break

            text = data.decode(errors="replace")
            parts.append(text)
            last_output_at = loop.time()

            # Kalau potongan output berisi menu/prompt, biasanya masih ada sisa render.
            # Kasih grace period lalu lanjut baca lagi, bukan langsung kirim ke Telegram.
            if any(hint.lower() in text.lower() for hint in PROMPT_HINTS):
                await asyncio.sleep(CLI_PROMPT_GRACE)

        return clean_output("".join(parts))


sessions: dict[int, CliSession] = {}


def get_session(user_id: int) -> CliSession:
    if user_id not in sessions:
        sessions[user_id] = CliSession(user_id=user_id)
    return sessions[user_id]


async def send_cli_output(update: Update, text: str):
    target = update.effective_message
    text = clean_output(text)
    text, qris_images = extract_qris_image_paths(text)
    text, dana_links = extract_dana_payment_links(text)

    if text:
        for chunk in chunk_text(text):
            await target.reply_text(f"<pre>{html.escape(chunk)}</pre>", parse_mode=ParseMode.HTML)

    for link in dana_links:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 Bayar DANA", url=link)]
        ])
        await target.reply_text(
            "🔗 Link pembayaran DANA:",
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )

    if not text and not qris_images and not dana_links:
        await target.reply_text("✅ Input diproses. Kalau tidak ada output, kemungkinan CLI sedang menunggu input lanjutan.")

    for image_path in qris_images:
        try:
            if not image_path.exists():
                await target.reply_text(f"⚠️ File QRIS tidak ketemu: <code>{html.escape(str(image_path))}</code>", parse_mode=ParseMode.HTML)
                continue
            with image_path.open("rb") as photo:
                await target.reply_photo(photo=photo, caption="✅ QRIS siap discan")
        except Exception as exc:
            logger.exception("Gagal kirim gambar QRIS")
            await target.reply_text(f"⚠️ Gagal kirim gambar QRIS: <code>{html.escape(str(exc))}</code>", parse_mode=ParseMode.HTML)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.effective_message.reply_text(f"⛔ User ID {user_id} tidak diizinkan.")
        return

    text = format_profile()
    if update.callback_query:
        await update.callback_query.edit_message_text(text=text, reply_markup=build_menu_keyboard(), parse_mode=ParseMode.HTML)
    else:
        await update.effective_message.reply_text(text=text, reply_markup=build_menu_keyboard(), parse_mode=ParseMode.HTML)


async def user_id_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(f"User ID kamu: {update.effective_user.id}")


async def restart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.effective_message.reply_text("⛔ Tidak diizinkan.")
        return
    output = await get_session(user_id).start()
    await update.effective_message.reply_text("♻️ CLI direstart.")
    await send_cli_output(update, output)


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in sessions:
        await sessions[user_id].stop()
    await update.effective_message.reply_text("🛑 Session CLI dihentikan.")


async def cek_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.effective_message.reply_text("⛔ Tidak diizinkan.")
        return
    session = get_session(user_id)
    output = await session.read_available(timeout=8.0)
    await send_cli_output(update, output or "Belum ada output baru dari CLI.")


async def run_wa_qris_flow(update: Update, user_id: int):
    """Auto flow WA QRIS.

    Urutan sesuai CLI:
    6 -> famcode dari .env -> nomor paket -> QRIS -> nominal payment.
    Default: 6, WA_QRIS_FAMCODE, 4, 3, 5000.
    """
    session = get_session(user_id)
    steps = [
        ("6", "🔍 Membuka menu FamCode..."),
        (WA_QRIS_FAMCODE, "📨 Mengirim kode dari .env..."),
        (WA_QRIS_PACKAGE_NUMBER, f"📦 Memilih nomor paket {WA_QRIS_PACKAGE_NUMBER}..."),
        (WA_QRIS_PAYMENT_NUMBER, f"💳 Memilih metode pembayaran QRIS nomor {WA_QRIS_PAYMENT_NUMBER}..."),
        (WA_QRIS_PAYMENT_AMOUNT, f"💰 Mengisi nominal payment {WA_QRIS_PAYMENT_AMOUNT}..."),
    ]
    combined = []
    await update.effective_message.reply_text("⚡ Menjalankan WA QRIS otomatis...")

    for value, label in steps:
        await update.effective_message.reply_text(label)
        out = await session.send(value)
        if out:
            combined.append(out)

        # Kasih jeda karena beberapa step fetching API dulu sebelum prompt berikutnya muncul.
        # Ya, CLI-nya perlu diperlakukan seperti mesin fotokopi kecapekan.
        await asyncio.sleep(0.9)
        more = await session.read_available(timeout=READ_TIMEOUT)
        if more:
            combined.append(more)

    # QRIS biasanya muncul setelah nominal payment dikirim, jadi baca sedikit lebih lama.
    final_more = await session.read_available(timeout=max(READ_TIMEOUT, 25.0))
    if final_more:
        combined.append(final_more)

    await send_cli_output(update, "\n\n".join(combined) or "Flow WA QRIS selesai, tapi CLI tidak ngasih output baru.")


async def run_wa_dana_flow(update: Update, user_id: int):
    """Auto flow WA DANA / e-wallet.

    Urutan sesuai CLI:
    6 -> famcode dari .env -> nomor paket -> e-wallet -> DANA -> nomor DANA -> nominal payment.
    Default: 6, WA_DANA_FAMCODE, 4, 2, 1, 081358238538, 5000.
    """
    session = get_session(user_id)
    steps = [
        ("6", "🔍 Membuka menu FamCode..."),
        (WA_DANA_FAMCODE, "📨 Mengirim kode dari .env..."),
        (WA_DANA_PACKAGE_NUMBER, f"📦 Memilih nomor paket {WA_DANA_PACKAGE_NUMBER}..."),
        (WA_DANA_PAYMENT_NUMBER, f"💳 Memilih metode pembayaran e-wallet nomor {WA_DANA_PAYMENT_NUMBER}..."),
        (WA_DANA_EWALLET_NUMBER, f"💙 Memilih DANA nomor {WA_DANA_EWALLET_NUMBER}..."),
        (WA_DANA_MSISDN, f"📱 Mengisi nomor DANA {WA_DANA_MSISDN}..."),
        (WA_DANA_PAYMENT_AMOUNT, f"💰 Mengisi nominal payment {WA_DANA_PAYMENT_AMOUNT}..."),
    ]
    combined = []
    await update.effective_message.reply_text("💳 Menjalankan WA DANA otomatis...")

    for value, label in steps:
        await update.effective_message.reply_text(label)
        out = await session.send(value)
        if out:
            combined.append(out)

        await asyncio.sleep(0.9)
        more = await session.read_available(timeout=READ_TIMEOUT)
        if more:
            combined.append(more)

    # Link/payment DANA biasanya muncul setelah nominal dikirim, jadi baca sedikit lebih lama.
    final_more = await session.read_available(timeout=max(READ_TIMEOUT, 25.0))
    if final_more:
        combined.append(final_more)

    await send_cli_output(update, "\n\n".join(combined) or "Flow WA DANA selesai, tapi CLI tidak ngasih output baru.")


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not is_allowed(user_id):
        await query.message.reply_text(f"⛔ User ID {user_id} tidak diizinkan.")
        return

    data = query.data or ""
    if not data.startswith("menu:"):
        return
    choice = data.split(":", 1)[1]

    if choice == "refresh":
        await start(update, context)
        return
    if choice == "restart":
        output = await get_session(user_id).start()
        await query.message.reply_text("♻️ CLI direstart.")
        await send_cli_output(update, output)
        return
    if choice == "wa_qris":
        await run_wa_qris_flow(update, user_id)
        return

    if choice == "wa_dana":
        await run_wa_dana_flow(update, user_id)
        return

    send_value = MENU_ALIASES.get(choice, choice)
    await query.message.reply_text(f"▶️ Menjalankan menu: <code>{html.escape(choice)}</code>", parse_mode=ParseMode.HTML)
    output = await get_session(user_id).send(send_value)
    await send_cli_output(update, output)


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.effective_message.reply_text(f"⛔ User ID {user_id} tidak diizinkan.")
        return
    text = update.effective_message.text or ""
    output = await get_session(user_id).send(text)
    await send_cli_output(update, output)


async def on_shutdown(app):
    for session in sessions.values():
        await session.stop()


def main_bot():
    if not BOT_TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN belum di-set di .env")
        return

    application = ApplicationBuilder().token(BOT_TOKEN).post_shutdown(on_shutdown).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", start))
    application.add_handler(CommandHandler("id", user_id_cmd))
    application.add_handler(CommandHandler("restart", restart_cmd))
    application.add_handler(CommandHandler("stop", stop_cmd))
    application.add_handler(CommandHandler("cek", cek_cmd))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    print("Bot Telegram berjalan. Buka Telegram lalu kirim /start")
    application.run_polling()


if __name__ == "__main__":
    main_bot()
