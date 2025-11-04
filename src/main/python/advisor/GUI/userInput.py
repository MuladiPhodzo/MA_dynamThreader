import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import ttkbootstrap as tb
from ttkbootstrap.constants import *
import datetime
import queue
import time
import sys


import json
import os

from Client import mt5Client

CONFIG_FILE = "user_config.json"

# ==========================================================
#              TEXT REDIRECTOR (stdout -> GUI)
# ==========================================================
class TextRedirector:
    """Redirects print statements to both GUI and console."""

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


# ==========================================================
#                  LOG WINDOW (Child GUI)
# ==========================================================
class LogWindow:
    """Window displaying live logs and bot summary."""

    paused = False

    def __init__(self, master: tb.Window):
        # --- Window ---
        self.window = tk.Toplevel(master) if master else tk.Window()
        self.window.title("📢 Trading Advisor Bot — Running")
        self.window.geometry("1440x840")

        self.queue = queue.Queue()
        self._build_main_layout()
        self._build_live_log_panel()
        self._build_summary_panel()

        # --- Poll queue + redirector ---
        self.poll_queue()
        self.redirector = TextRedirector(self)

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

        self.log_area_frame = tb.LabelFrame(live_panel, text="📡 Live Logs", padding=10)
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

        tb.Button(btn_frame, text="❌ Stop Bot", bootstyle=DANGER, command=self.quit).pack(side="left", padx=10)
        self.pause_button = tb.Button(
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
            "Daily": "+12%",
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
                "Total Trades Executed": 12,
                "Trades in Profit": 7,
                "Trades in Loss": 5,
                "Account % Change": "+3.4%",
            },
            "Risk Metrics": {
                "Max Drawdown": "-2.1%",
                "Sharpe Ratio": "1.45",
                "Win Rate": "58%",
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

        toggle_btn = tb.Button(section_frame, text=f"▶ {title}", bootstyle=INFO, width=20)
        toggle_btn.pack(fill="x")

        content_frame = tb.Frame(section_frame)
        content_frame.pack(fill="x", padx=10, pady=2)
        content_frame.visible = True

        for key, value in data.items():
            tb.Label(content_frame, text=f"{key}: {value}", anchor="w").pack(fill="x")

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
            print("⏸ Bot paused.")
        else:
            self.pause_button.config(text="⏸ Pause Bot", bootstyle=INFO)
            print("▶ Bot resumed.")

    def quit(self):
        print("🛑 Stopping bot...")
        self.window.destroy()
        if hasattr(self, "stop_callback"):
            self.stop_callback()

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
        self.root.title("🚀 Trading Bot Setup")
        self.root.geometry("700x650")
        
        self.userDataSample = {
            'volume': 0.1,
            'sl': 250,
            'rr': '1:2',
            'tp': {'LTF': '30M', 'HTF': '1H'},
            "account_id": 308826480,
            "password": "N3gus5@1111",
            "server": "XMGlobal-MT5 6" 
        }

        self.user_data = {}
        self.log_window = None
        self._build_main_ui()
        self._load_user_config()
        
    def pop_up_error(self, message: str):
        messagebox.showerror("Input Error", message)
        
    def prompt_window(self, message: str):
        return messagebox.askyesno("Confirmation", message)

    # ----------------------------------------------------------
    def _build_main_ui(self):
        main_frame = tb.Frame(self.root, padding=20)
        main_frame.pack(fill="both", expand=True)

        tb.Label(main_frame, text="Trading Bot Setup", font=("Segoe UI", 16, "bold")).pack(pady=(0, 15))

        trading_frame = tb.Labelframe(main_frame, text="⚙️ Trading Setup", padding=10)
        trading_frame.pack(fill="x", pady=10)

        self.volume = tb.Spinbox(trading_frame, from_=0.01, to=5.0, increment=0.01, width=10)
        self._add_field(trading_frame, "Volume", self.volume)

        self.sl = tb.Spinbox(trading_frame, from_=1, to=1000, increment=1, width=10)
        self._add_field(trading_frame, "SL Distance", self.sl)

        self.rr = tb.Combobox(trading_frame, values=["1:1", "1:2", "1:3", "1:5"], width=10)
        self.rr.current(2)
        self._add_field(trading_frame, "RR Ratio", self.rr)

        timeframes = tb.Labelframe(trading_frame, text="Timeframes", width=10, padding=(10, 5))
        timeframes.pack(fill="x", pady=5)
        self.tf_primary = tb.Combobox(timeframes, values=["1M", "5M", "15M", "30M", "1H", "4H", "1D"], width=10)
        self.tf_primary.current(3)
        self.tf_primary.pack(side="left", padx=(0, 5))
        self.tf_secondary = tb.Combobox(timeframes, values=["1M", "5M", "15M", "30M", "1H", "4H", "1D"], width=10)
        self.tf_secondary.current(4)
        self.tf_secondary.pack(side="left", padx=(0, 5))

        account_frame = tb.Labelframe(main_frame, text="👤 Account Info", padding=10)
        account_frame.pack(fill="x", pady=10)

        self.server = tb.Entry(account_frame, width=25)
        self._add_field(account_frame, "Server", self.server)
        self.account_id = tb.Entry(account_frame, width=25)
        self._add_field(account_frame, "Account ID", self.account_id)
        self.password = tb.Entry(account_frame, show="●", width=25)
        self._add_field(account_frame, "Password", self.password)

        # ✅ Remember Me
        self.remember_me = tk.BooleanVar()
        tb.Checkbutton(main_frame, text="Remember Me", variable=self.remember_me, bootstyle="info").pack(anchor="w", pady=(5, 10))

        btn_frame = tb.Frame(main_frame)
        btn_frame.pack(pady=15)
        tb.Button(btn_frame, text="▶ Run Bot", bootstyle=SUCCESS, command=self.start_bot).pack(side="left", padx=10)
        tb.Button(btn_frame, text="❌ Stop Bot", bootstyle=DANGER, command=self.stop_bot).pack(side="left", padx=10)

        self.status = tb.Label(self.root, text="Ready", bootstyle=INFO, anchor="w")
        self.status.pack(side="bottom", fill="x")

    def _add_field(self, parent, label, widget: tk.Widget):
        frame = tb.Frame(parent)
        frame.pack(fill="x", pady=5)
        tb.Label(frame, text=label, width=15, anchor="e").pack(side="left", padx=5)
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
                "sl_distance": sl,
                "rr_ratio": rr,
                "tf_primary": self.tf_primary.get(),
                "tf_secondary": self.tf_secondary.get(),
                "server": server,
                "account_id": account_id,
                "password": password,
            }
            return True
        except Exception as e:
            messagebox.showerror("Input Error", str(e))
            return
        
    # ----------------------------------------------------------
    # Remember Me Logic
    # ----------------------------------------------------------
    def _load_user_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    config = json.load(f)
                self.server.insert(0, config.get("server", ""))
                self.account_id.insert(0, config.get("account_id", ""))
                self.password.insert(0, config.get("password", ""))
                self.remember_me.set(True)
            except Exception as e:
                print(f"⚠ Failed to load config: {e}")

    def _save_user_config(self):
        if self.remember_me.get():
            data = {
                "server": self.server.get(),
                "account_id": self.account_id.get(),
                "password": self.password.get(),
            }
            with open(CONFIG_FILE, "w") as f:
                json.dump(data, f)
        elif os.path.exists(CONFIG_FILE):
            os.remove(CONFIG_FILE)
    # ----------------------------------------------------------
    # Bot Logic
    # ----------------------------------------------------------
    def start_bot(self):
        if not self.validate_inputs():
            return
        print("🚀 Starting bot...")
        self.root.withdraw()
        UserGUI.should_run = True
        self.status.config(text="🚀 Bot Running...")
        self.log_window = LogWindow(self.root)
        sys.stdout = self.log_window.redirector
        sys.stderr = self.log_window.redirector

    def stop_bot(self):
        UserGUI.should_run = False
        self.status.config(text="🛑 Bot Stopped", state='disabled')
        self.should_run = False  # GUI flag update
        
        if hasattr(self, "stop_callback"):
            self.stop_callback()  # call linked stop function from RunAdvisorBot
            
        self._save_user_config()
        self.root.destroy()
        
    def set_stop_callback(self, callback):
        """Allow main bot to inject a function to stop everything."""
        self.stop_callback = callback


