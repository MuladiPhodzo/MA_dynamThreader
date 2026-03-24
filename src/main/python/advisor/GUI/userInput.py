import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import ttkbootstrap as tb
from ttkbootstrap.constants import DANGER, INFO, SUCCESS

import datetime
import queue
import sys
import json
import os
from advisor.utils.logging_setup import get_logger
from advisor.Trade.tradeStats import TradeStats as Stats
from advisor.Client.mt5Client import MetaTrader5Client
CONFIG_FILE = "configs.json"

logger = get_logger("SetUp Wizard")
# ==========================================================
#                   MAIN SETUP WINDOW
# ==========================================================
class setUpWizard:
    should_run = False

    def __init__(self, client: MetaTrader5Client):
        self.root = tb.Window(themename="cosmo")
        self.client = client
        self.root.title("🚀 EMA8t setup Wizard")
        self.root.geometry("400x350")

        self.user_data = {}
        self.bot_cfg = {}
        self.log_window = None
        self._build_main_ui()
        # if CONFIG_FILE and os.path.exists(CONFIG_FILE):
        #     proceed = self.prompt_window(
        #         "Existing configuration found. run previous configuration?")
        #     if proceed:
        #         self._load_user_config()
        #         self._sync_ui_to_user_data()
        #         self.start_bot()

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

        #  ----------------------------------------------------------------------------
        #  TRADING CONFIGS FRAME
        #  ----------------------------------------------------------------------------
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

        self.trailing_sl = tk.BooleanVar()
        tb.Checkbutton(trading_frame, text="Trailing SL", variable=self.trailing_sl,
                       bootstyle="info", ).pack(anchor="w", pady=(5, 10))

        #  ----------------------------------------------------------------------------
        #  ACCOUNT CREDS FRAME
        #  ----------------------------------------------------------------------------
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
        self.root.mainloop()

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
                "creds": {
                    "server": server,
                    "account_id": account_id,
                    "password": password,
                },
                "trade_cfg": {
                    "volume": volume,
                    "pip_distance": sl,
                    "rr_ratio": rr,
                    "trailing_sl": bool(self.trailing_sl.get()),
                },
            }
            return True
        except Exception as e:
            messagebox.showerror("Input Error", str(e))
            return

    def _sync_ui_to_user_data(self):
        """Synchronize widget values back to self.user_data."""
        try:
            self.user_data = {
                "creds": {
                    "server": self.server.get(),
                    "account_id": int(self.account_id.get()),
                    "password": self.password.get(),
                },
                "trade_cfg": {
                    "volume": float(self.volume.get()),
                    "pip_distance": int(self.sl.get()),
                    "rr_ratio": self.rr.get(),
                    "trailing_sl": bool(self.trailing_sl.get()),
                },
            }
        except Exception as e:
            logger.info(f"⚠️ Failed to sync UI to user_data: {e}")

    # ----------------------------------------------------------
    # Remember Me Logic
    # ----------------------------------------------------------
    def _load_user_config(self):
        """Load saved user configuration and populate GUI widgets."""
        if not os.path.exists(CONFIG_FILE):
            logger.info("No config file to load.")
            return

        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
            trade_cfg: dict = data.get("trade_cfg", {})
            creds: dict = data.get("creds", {})

            cleaned_data = {
                "volume": str(trade_cfg.get("volume", "0.01")),
                "sl_distance": int(trade_cfg.get("pip_distance", 200)),
                "rr_ratio": str(trade_cfg.get("rr_ratio", "1:2")),
                "server": str(creds.get("server", "")),
                "account_id": str(creds.get("account_id", "")),
                "password": str(creds.get("password", "")),
            }

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

            self.user_data = {
                "creds": {
                    "server": cleaned_data["server"],
                    "account_id": int(cleaned_data["account_id"]),
                    "password": cleaned_data["password"],
                },
                "trade_cfg": {
                    "volume": float(cleaned_data["volume"]),
                    "pip_distance": int(cleaned_data["sl_distance"]),
                    "rr_ratio": cleaned_data["rr_ratio"],
                    "trailing_sl": bool(self.trailing_sl.get()),
                },
            }

            logger.info("User config loaded successfully.")

        except Exception as e:
            logger.info(f"Failed to load config: {e}")

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
                "trade_cfg": {
                    "volume": clean_value(self.volume.get()),
                    "pip_distance": clean_value(int(self.sl.get())),
                    "rr_ratio": clean_value(self.rr.get()),
                    "trailing_sl": clean_value(self.trailing_sl.get())}
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
            self.client.trade_cfg = self.user_data["trade_cfg"]
            self.root.destroy()
            self.status.config(text="🚀 Bot Running...")

    def stop_bot(self):
        self.status.config(text="🛑 Bot Stopped", state='disabled')
        self.should_run = False  # GUI flag update

        if hasattr(self, "stop_callback"):
            self.stop_callback()  # call linked stop function from RunAdvisorBot
        sys.exit(0)

    def set_stop_callback(self, callback):
        """Allow main bot to inject a function to stop everything."""
        self.stop_callback = callback
