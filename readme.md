# Bot Trading Demo Binance Futures

Bot trading Python profesional dan modular untuk **Binance USD-M Futures Demo Trading**. Bot ini mendukung trading futures demo Binance yang terautentikasi, paper trading, data market REST, harga websocket opsional, sinyal EMA crossover, kontrol risiko, notifikasi Telegram, dan mode backtest sederhana.

Repository ini sengaja dibuat **bukan untuk uang sungguhan**. CCXT tidak lagi mendukung panggilan Binance futures terautentikasi melalui jalur sandbox/testnet lama, sehingga trading futures terautentikasi menggunakan Binance Demo Trading melalui `enable_demo_trading(True)`. Pemeriksaan endpoint tetap menolak host Binance Futures sungguhan.

## Struktur Proyek

```text
bot/
|-- main.py
|-- strategy.py
|-- indicators.py
|-- trader.py
|-- config.py
|-- logger.py
|-- requirements.txt
|-- .env.example
|-- README.md
`-- logs/
    |-- .gitkeep
    `-- sample.log
```

## Setup

Gunakan **Python 3.13 atau versi lebih baru**. Package `pandas-ta` yang tersedia saat ini melalui pip membutuhkan Python 3.12+, dan Python 3.13 memiliki dukungan package 64-bit yang baik pada mesin ini. Python 3.8 tidak dapat menginstal versi pandas yang dibutuhkan dan juga akan gagal pada sintaks type-hint modern yang digunakan oleh bot.

1. Buat dan aktifkan virtual environment:

```bash
py -3.13 -m venv .venv
.venv\Scripts\activate
```

Jika PowerShell memblokir aktivasi, jalankan Python dari venv secara langsung:

```bash
.\.venv\Scripts\python.exe main.py
```

2. Install dependency:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

3. Buat file environment lokal:

```bash
copy .env.example .env
```

4. Tambahkan kredensial API Binance Demo Trading ke `.env` hanya jika ingin menggunakan `MODE=live`.

Buat key tersebut dari **Binance Demo Trading API Management**, bukan dari dashboard Futures Testnet lama dan bukan dari akun Binance asli.

## Menjalankan Bot

Dari direktori `bot`:

```bash
python main.py
```

Konfigurasi yang direkomendasikan untuk percobaan pertama:

```env
MODE=paper
EXCHANGE_ENV=demo
USE_SANDBOX=true
```

Mode backtest:

```env
MODE=backtest
EXCHANGE_ENV=demo
USE_SANDBOX=true
```

Eksekusi futures demo Binance terautentikasi:

```env
MODE=live
EXCHANGE_ENV=demo
API_KEY=your_demo_trading_key
API_SECRET=your_demo_trading_secret
USE_SANDBOX=true
```

## Strategi

Strategi default yang digunakan adalah **EMA crossover**:

- Fast EMA: 9
- Slow EMA: 21
- Sinyal BUY: EMA 9 memotong ke atas EMA 21
- Sinyal SELL: EMA 9 memotong ke bawah EMA 21

Bot membuka satu posisi futures dalam satu waktu. Sinyal BUY akan membuka atau mempertahankan posisi long. Sinyal SELL akan membuka atau mempertahankan posisi short. Jika sinyal berlawanan muncul saat posisi sedang terbuka, bot akan keluar dari posisi saat ini dan menunggu cooldown sebelum membuka trade berikutnya.

## Manajemen Risiko

Kontrol risiko dikonfigurasi melalui `.env`:

- `RISK_PER_TRADE`: proporsi saldo akun yang dirisikokan per entry
- `TRADE_MARGIN_USDT`: margin tetap per trade. Isi `1000` untuk menggunakan sekitar 1000 USDT margin per entry. Isi `0` untuk menggunakan sizing berbasis risiko.
- `MAX_DAILY_LOSS`: batas kerugian realisasi harian yang akan memblokir entry baru
- `STOP_LOSS_PCT`: persentase stop loss lokal
- `TAKE_PROFIT_PCT`: persentase take profit lokal berdasarkan pergerakan harga
- `TAKE_PROFIT_ON_MARGIN_PCT`: take profit opsional berbasis margin. Isi `0.10` untuk sekitar 10% profit dari margin. Bot akan mengonversinya menjadi pergerakan harga dengan membagi nilai tersebut menggunakan `LEVERAGE`.
- `LEVERAGE`: pengaturan leverage futures
- `COOLDOWN_SECONDS`: jeda setelah entry dan exit
- `MAX_NOTIONAL_PCT`: membatasi nilai notional posisi relatif terhadap saldo leverage yang tersedia

Stop loss dan take profit dipantau secara lokal oleh bot yang sedang berjalan. Untuk penggunaan produksi sungguhan, pertimbangkan menambahkan order protektif **reduce-only** langsung di exchange setelah setiap entry.

## Panduan Modul

- `config.py`: Memuat environment variable, memvalidasi pengaturan, dan memastikan operasi tetap aman serta bukan menggunakan uang sungguhan.
- `logger.py`: Mengatur log console dan rotating file logs.
- `indicators.py`: Mengubah data OHLCV dari CCXT menjadi DataFrame pandas dan menambahkan kolom EMA dari pandas-ta.
- `strategy.py`: Berisi mesin sinyal EMA crossover dan backtester opsional.
- `trader.py`: Menangani koneksi exchange CCXT, pemeriksaan endpoint demo, market order, pemeriksaan risiko, simulasi paper trading, notifikasi Telegram, harga websocket, retry, dan statistik performa.
- `main.py`: Menghubungkan semua komponen, menangani shutdown dengan aman, menjalankan loop live/paper, atau menjalankan backtest.

## Contoh Log

```text
2026-05-12 09:15:00 | INFO | binance_futures_testnet_bot | Connected to Binance Futures DEMO environment.
2026-05-12 09:15:01 | INFO | binance_futures_testnet_bot | Bot started in PAPER mode for BTC/USDT:USDT.
2026-05-12 09:15:16 | INFO | binance_futures_testnet_bot | STATUS | price=64250.10 | signal=HOLD | position=none | balance=1000.00 | total_trades=0 | winrate=0.00% | pnl=0.00 | drawdown=0.00%
2026-05-12 09:18:31 | INFO | binance_futures_testnet_bot | ENTRY | LONG | amount=0.029571 | price=64250.10 | sl=63607.60 | tp=65535.10
2026-05-12 09:27:46 | INFO | binance_futures_testnet_bot | EXIT | LONG | reason=take_profit | entry=64250.10 | exit=65535.10 | pnl=37.99 | total_trades=1 | winrate=100.00% | pnl=37.99 | drawdown=0.00%
```

## Perintah Telegram

Ketika `TELEGRAM_TOKEN` dan `TELEGRAM_CHAT_ID` dikonfigurasi, bot juga akan mendengarkan perintah dari chat tersebut:

```text
/status
/help
```

`/status` akan membalas status runtime terbaru yang tersimpan di cache, seperti mode, symbol, uptime, waktu update terakhir, harga, sinyal, posisi, saldo, leverage, margin per trade, take profit, stop loss, dan statistik performa.

Perintah Telegram ditangani oleh proses Python yang sedang berjalan. Jika service berhenti atau crash, bot tidak dapat menjawab `/status`; dalam kondisi tersebut, gunakan `sudo systemctl status crypto-bot` atau `journalctl -u crypto-bot -f` pada VM.

## Catatan Keamanan Penting

- Jangan pernah memasukkan kredensial Binance asli ke `.env`.
- Gunakan kredensial API Binance Demo Trading untuk `MODE=live`.
- Tetap gunakan `EXCHANGE_ENV=demo` untuk panggilan API futures terautentikasi.
- Tetap gunakan `USE_SANDBOX=true`.
- Likuiditas dan eksekusi pada demo bisa berbeda dari market sungguhan.
- Software ini dibuat untuk edukasi, bukan nasihat keuangan.

## Contoh: Margin 1000 USDT dan TP 10%

Untuk leverage 50x, profit 10% dari margin hanya sekitar pergerakan harga 0,2%:

```text
10% / 50 = 0.2%
```

Gunakan:

```env
LEVERAGE=50
TRADE_MARGIN_USDT=1000
TAKE_PROFIT_ON_MARGIN_PCT=0.10
```

Dengan pengaturan ini, bot akan menghitung harga take-profit yang benar berdasarkan leverage. Kamu tidak perlu mengatur `TAKE_PROFIT_PCT=0.002` secara manual.
