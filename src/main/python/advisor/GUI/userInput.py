import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import ttkbootstrap as tb
from ttkbootstrap.constants import DANGER, INFO, SUCCESS

import datetime
import queue
import sys
import json
import os
import logging
from advisor.Trade.tradeStats import TradeStats as Stats
CONFIG_FILE = "user_config.json"

# -------------------------
# Logging Configuration
# -------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("MA_DynamAdvisor.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger(__name__)

# ==========================================================
#              TEXT REDIRECTOR (stdout -> GUI)
# ==========================================================

class TextRedirector:
    """Redirects logger.info statements to both GUI and console."""

    def __init__(self, log_window):
        self.log_window = log_window
        self.stdout = sys.stdout
        sys.stdout = self  # redirect global output

    def write(self, message):
        message = message.strip()
        if message:
            try:
                timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.log_window.queue.put(f"[{timestamp}] {message}")
            except Exception:
                pass
            self.stdout.write(message + "\n")
            self.stdout.flush()

    def flush(self):
        self.stdout.flush()

    def restore(self):
        sys.stdout = self.stdout

class QueueLoggerHandler(logging.Handler):
    """Redirects Python logger messages to the Tkinter GUI queue."""
    def __init__(self, log_window):
        super().__init__()
        self.log_window = log_window

    def emit(self, record):
        try:
            msg = self.format(record)
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            formatted = f"[{timestamp}] {msg}"
            self.log_window.queue.put(formatted)
        except Exception:
            self.handleError(record)

# ==========================================================
#                  LOG WINDOW (Child GUI)
# ==========================================================
class LogWindow:
    """Window displaying live logs and bot summary."""

    paused = False

    def __init__(self, master: tb.Window):
        self.window = tk.Toplevel(master) if master else tk.Window()
        self.window.title("📢 Trading Advisor Bot — Running")
        self.window.geometry("1440x840")

        self.queue = queue.Queue()

        self.stats = Stats()
        self.stop_callback = None
        self._build_main_layout()
        self._build_live_log_panel()
        self._build_summary_panel()

        self.poll_queue()
        self.redirector = TextRedirector(self)

        # ✅ Attach the logger handler
        self.logger_handler = QueueLoggerHandler(self)
        formatter = logging.Formatter("%(levelname)s: %(message)s")
        self.logger_handler.setFormatter(formatter)

        root_logger = logging.getLogger()
        root_logger.addHandler(self.logger_handler)
        root_logger.setLevel(logging.INFO)

    # ----------------------------------------------------------
    # LAYOUT
    # ----------------------------------------------------------
    def _build_main_layout(self):
        self.main_frame = tb.Frame(self.window)
        self.main_frame.pack(fill="both", expand=True, padx=10, pady=10)
        self.main_frame.columnconfigure(0, weight=2)
        self.main_frame.columnconfigure(1, weight=1)
        self.main_frame.rowconfigure(0, weight=1)

    def _build_live_log_panel(self):
        live_panel = tb.Frame(self.main_frame)
        live_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 5))

        self.log_area_frame = tb.LabelFrame(
            live_panel, text="📡 Live Logs", padding=10)
        self.log_area_frame.pack(fill="both", expand=True)

        self.log_area = scrolledtext.ScrolledText(
            self.log_area_frame,
            wrap=tk.WORD,
            font=("Consolas", 10),
            bg="#000000",
            fg="#00ff11",
            state="disabled",
        )
        self.log_area.pack(fill="both", expand=True)

        # Buttons
        btn_frame = tb.Frame(live_panel)
        btn_frame.pack(pady=5)

        ttk.Button(btn_frame, text="❌ Stop Bot", bootstyle=DANGER,
                   command=self.quit).pack(side="left", padx=10)
        self.pause_button = ttk.Button(
            btn_frame,
            text="⏸ Pause Bot",
            bootstyle=INFO,
            command=self.toggle_pause,
        )
        self.pause_button.pack(side="left", padx=10)

    def _build_summary_panel(self):
        summary_panel = tb.LabelFrame(
            self.main_frame, text="📜 Trading Bot Summary", padding=10
        )
        summary_panel.grid(row=0, column=1, sticky="nsew", padx=(5, 0))

        self.summary_area = tk.Text(
            summary_panel,
            wrap=tk.WORD,
            bg="#1e1e1e",
            fg="#00ff00",
            height=15,
            state="disabled",
        )
        self.summary_area.pack(fill="both", expand=True)
        self._load_sample_summary()

        self.summary_container = tb.Frame(summary_panel)
        self.summary_container.pack(fill="both", expand=True, pady=10)

        self.add_collapsible_section("Change Rates", {
            "Daily": f"{4}%",
            "Weekly": "+7%",
            "Monthly": "-5%",
        })
        self.add_collapsible_section("Charts", {
            "Percentage Change": "+3.4%",
        })

    # ----------------------------------------------------------
    # UTILITIES
    # ----------------------------------------------------------
    def _load_sample_summary(self):
        summary_data = {
            "Performance": {
                "Total Trades Executed": self.stats.num_trades,
                "Trades in Profit": self.stats.tradesInProfit,
                "Trades in Loss": self.stats.loss,
                "Account % Change": f"{self.stats.accountChangePercent}%",
            },
            "Risk Metrics": {
                "Max Drawdown": f"{self.stats.drawdown}",
                "Sharpe Ratio": f"{self.stats.sharpeRatio}",
                "Win Rate": f"{self.stats.winRate}%",
            },
        }

        self.summary_area.config(state="normal")
        self.summary_area.delete("1.0", tk.END)
        self.summary_area.insert(tk.END, "📊 Trading Performance Summary\n\n")

        for key, values in summary_data.items():
            self.summary_area.insert(tk.END, f"{key}:\n")
            for metric, val in values.items():
                self.summary_area.insert(tk.END, f"   - {metric}: {val}\n")
            self.summary_area.insert(tk.END, "\n")
        self.summary_area.config(state="disabled")

    def add_collapsible_section(self, title, data: dict):
        section_frame = tb.Frame(self.summary_container)
        section_frame.pack(fill="x", pady=5)

        toggle_btn = tb.Button(
            section_frame, text=f"▶ {title}", bootstyle=INFO, width=20)
        toggle_btn.pack(fill="x")

        content_frame = tb.Frame(section_frame)
        content_frame.pack(fill="x", padx=10, pady=2)
        content_frame.visible = True

        for key, value in data.items():
            tb.Label(content_frame, text=f"{key}: {value}", anchor="w").pack(
                fill="x")

        def toggle():
            if content_frame.visible:
                content_frame.pack_forget()
                toggle_btn.config(text=f"▼ {title}")
            else:
                content_frame.pack(fill="x", padx=10, pady=2)
                toggle_btn.config(text=f"▶ {title}")
            content_frame.visible = not content_frame.visible

        toggle_btn.config(command=toggle)

    # ----------------------------------------------------------
    # LOG + CONTROL
    # ----------------------------------------------------------
    def poll_queue(self):
        try:
            while True:
                message = self.queue.get_nowait()
                self._append_log(message)
            # end while
        except queue.Empty:
            pass
        self.window.after(100, self.poll_queue)

    def _append_log(self, message: str):
        self.log_area.config(state="normal")
        self.log_area.insert(tk.END, message + "\n")
        self.log_area.see(tk.END)
        self.log_area.config(state="disabled")

    def toggle_pause(self):
        LogWindow.paused = not LogWindow.paused
        if LogWindow.paused:
            self.pause_button.config(text="▶ Resume Bot", bootstyle=SUCCESS)
            logger.info("⏸ Bot paused.")
        else:
            self.pause_button.config(text="⏸ Pause Bot", bootstyle=INFO)
            logger.info("▶ Bot resumed.")

    def quit(self):
        logger.info("🛑 Stopping bot...")
        UserGUI.should_run = False
        root_logger = logging.getLogger()
        root_logger.removeHandler(self.logger_handler)
        self.window.destroy()
        if hasattr(self, "stop_callback"):
            self.set_stop_callback(self.stop_callback)

        self.window.destroy()
        sys.exit(0)

    def set_stop_callback(self, callback):
        """Allow main bot to inject a function to stop everything."""
        self.stop_callback = callback

# ==========================================================
#                   MAIN SETUP WINDOW
# ==========================================================
class UserGUI:
    should_run = False

    def __init__(self):
        self.root = tb.Window(themename="cosmo")
        self.root.title("🚀 EMA8t setup Wizard")
        self.root.geometry("700x650")

        self.user_data = {}
        self.bot_cfg = {}
        self.log_window = None
        self._build_main_ui()
        if CONFIG_FILE and os.path.exists(CONFIG_FILE):
            proceed = self.prompt_window(
                "Existing configuration found. run previous configuration?")
            if proceed:
                self._load_user_config()
                self._sync_ui_to_user_data()
                self.start_bot()

    def pop_up_error(self, message: str):
        messagebox.showerror("Input Error", message)

    def prompt_window(self, message: str):
        return messagebox.askyesno("Confirmation", message)

    # ----------------------------------------------------------
    def _build_main_ui(self):
        main_frame = tb.Frame(self.root, padding=20)
        main_frame.pack(fill="both", expand=True)

        tb.Label(main_frame, text="Trading Bot Setup", font=(
            "Segoe UI", 16, "bold")).pack(pady=(0, 15))

        trading_frame = tb.Labelframe(
            main_frame, text="⚙️ Trading Setup", padding=10)
        trading_frame.pack(fill="x", pady=10)

        self.volume = tb.Spinbox(
            trading_frame, from_=0.01, to=5.0, increment=0.01, width=10)
        self._add_field(trading_frame, "Volume", self.volume)

        self.sl = tb.Spinbox(trading_frame, from_=1,
                             to=1000, increment=1, width=10)
        self._add_field(trading_frame, "SL Distance", self.sl)

        self.rr = tb.Combobox(trading_frame, values=[
                              "1:1", "1:2", "1:3", "1:5"],
                              width=10)
        self.rr.current(2)
        self._add_field(trading_frame, "RR Ratio", self.rr)

        account_frame = tb.Labelframe(
            main_frame, text="👤 Account Info", padding=10)
        account_frame.pack(fill="x", pady=10)

        self.server = tb.Entry(account_frame, width=25)
        self._add_field(account_frame, "Server", self.server)

        self.account_id = tb.Entry(account_frame, width=25)
        self._add_field(account_frame, "Account ID", self.account_id)

        self.password = tb.Entry(account_frame, show="●", width=25)
        self._add_field(account_frame, "Password", self.password)

        # ✅ Remember Me
        self.remember_me = tk.BooleanVar()
        tb.Checkbutton(main_frame, text="Remember Me", variable=self.remember_me,
                       bootstyle="info", ).pack(anchor="w", pady=(5, 10))

        btn_frame = tb.Frame(main_frame)
        btn_frame.pack(pady=15)
        ttk.Button(btn_frame, text="▶ Run Bot", bootstyle=SUCCESS,
                   command=self.start_bot).pack(side="left", padx=10)
        ttk.Button(btn_frame, text="❌ Stop Bot", bootstyle=DANGER,
                   command=self.stop_bot).pack(side="left", padx=10)

        self.status = tb.Label(self.root, text="Ready",
                               bootstyle=INFO, anchor="w")
        self.status.pack(side="bottom", fill="x")

    def _add_field(self, parent, label, widget: tk.Widget):
        frame = tb.Frame(parent)
        frame.pack(fill="x", pady=5)
        tb.Label(frame, text=label, width=15,
                 anchor="e").pack(side="left", padx=5)
        widget.pack(side="left", padx=5)

    def validate_inputs(self):
        try:
            volume = float(self.volume.get())
            if volume <= 0:
                raise ValueError("Volume must be positive.")
            sl = int(self.sl.get())
            if sl <= 0:
                raise ValueError("SL Distance must be positive.")
            rr = self.rr.get()
            server = self.server.get().strip()
            account_id = int(self.account_id.get().strip())
            password = self.password.get().strip()
            if not server or not password:
                raise ValueError("Server and Password cannot be empty.")

            self.user_data = {
                "volume": volume,
                "sl": sl,
                "rr_ratio": rr,
                "tp": self.handleRR(),
                "server": server,
                "account_id": account_id,
                "password": password,
            }
            return True
        except Exception as e:
            messagebox.showerror("Input Error", str(e))
            return

    def _sync_ui_to_user_data(self):
        """Synchronize widget values back to self.user_data."""
        try:
            self.user_data = {
                "volume": self.volume.get(),
                "sl": self.sl.get(),
                "rr_ratio": self.rr.get(),
                "tp": self.handleRR(),
                "server": self.server.get(),
                "account_id": int(self.account_id.get()),
                "password": self.password.get(),
            }
        except Exception as e:
            logger.info(f"⚠️ Failed to sync UI to user_data: {e}")

    # ----------------------------------------------------------
    # Remember Me Logic
    # ----------------------------------------------------------
    def _load_user_config(self):
        """Load saved user configuration and populate GUI widgets."""
        if not os.path.exists(CONFIG_FILE):
            logger.info("⚠️ No config file to load.")
            return

        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
            configs: dict = data.get("configs")
            creds: dict = data.get("creds")

            # Ensure saved values are clean
            cleaned_data = {
                "volume": str(configs.get("trade_configs")("volume", "0.01")),
                "sl_distance": int(configs.get("trade_configs")("pip_distance", 200)),
                "rr_ratio": str(configs.get("trade_configs")("rr_ratio", "1:2")),
                "server": str(creds.get("creds")("server", "")),
                "account_id": str(creds.get("creds")("account_id", "")),  # convert int → string for GUI
                "password": str(creds.get("creds")("password", "")),
            }

            # Auto-detect widget type (StringVar or Entry)
            def set_val(widget, value):
                if hasattr(widget, "set"):
                    widget.set(value)
                elif hasattr(widget, "delete") and hasattr(widget, "insert"):
                    widget.delete(0, "end")
                    widget.insert(0, value)

            set_val(self.volume, cleaned_data["volume"])
            set_val(self.sl, cleaned_data["sl_distance"])
            set_val(self.rr, cleaned_data["rr_ratio"])
            set_val(self.server, cleaned_data["server"])
            set_val(self.account_id, cleaned_data["account_id"])
            set_val(self.password, cleaned_data["password"])

            # Persist loaded data internally
            self.user_data = cleaned_data

            logger.info("✅ User config loaded successfully.")

        except Exception as e:
            logger.info(f"❌ Failed to load config: {e}")

    def _save_user_config(self):
        import tempfile
        import shutil
        import re
        """Save user configuration safely and prevent duplicate concatenation."""
        if self.remember_me.get():
            def clean_value(v):
                """Remove repeated patterns and extra spaces."""
                v = str(v).strip()
                # Detect and reduce duplicated patterns like '1H1H1H' -> '1H'
                match = re.match(r"^(.+?)\1+$", v)
                if match:
                    v = match.group(1)
                return v

            self.user_data = {
                "creds": {
                    "server": clean_value(self.server.get()),
                    "account_id": clean_value(int(self.account_id.get())),
                    "password": clean_value(self.password.get())},
                "trade_configs": {
                    "volume": clean_value(self.volume.get()),
                    "pip_distance": clean_value(int(self.sl.get())),
                    "rr_ratio": clean_value(self.rr.get())}
            }

            tmp_file = tempfile.NamedTemporaryFile("w", delete=False, dir=os.path.dirname(CONFIG_FILE))
            json.dump(self.user_data, tmp_file, indent=4)
            tmp_file.close()
            shutil.move(tmp_file.name, CONFIG_FILE)

        elif os.path.exists(CONFIG_FILE):
            os.remove(CONFIG_FILE)

    def handleRR(self):
        tp_values = self.rr.get().split(":")
        if len(tp_values) != 2:
            raise ValueError("RR Ratio must be in format '1:2', '1:3', etc.")
        else:
            return self.multiplier(self.sl.get(), tp_values[1])

    def multiplier(self, val1, val2) -> int:
        return int(int(val1) * int(val2))
    # ----------------------------------------------------------
    # Bot Logic
    # ----------------------------------------------------------

    def start_bot(self):
        try:
            if not self.validate_inputs():
                return
        except Exception as e:
            self.pop_up_error(f"Input validation failed: {e}")
        finally:
            logger.info("🚀 Starting bot...")
            if self.remember_me.get():
                self._save_user_config()
            self.root.withdraw()
            UserGUI.should_run = True
            self.status.config(text="🚀 Bot Running...")
            self.log_window = LogWindow(self.root)
            self.log_window.set_stop_callback(self.stop_bot)
            sys.stdout = self.log_window.redirector
            sys.stderr = self.log_window.redirector

    def stop_bot(self):
        self.status.config(text="🛑 Bot Stopped", state='disabled')
        self.should_run = False  # GUI flag update

        if hasattr(self, "stop_callback"):
            self.stop_callback()  # call linked stop function from RunAdvisorBot
        sys.exit(0)

    def set_stop_callback(self, callback):
        """Allow main bot to inject a function to stop everything."""
        self.stop_callback = callback
