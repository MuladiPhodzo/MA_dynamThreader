import tkinter as tk
import ttkbootstrap as tb
from ttkbootstrap.constants import *
from tkinter.scrolledtext import ScrolledText
from tkinter import messagebox
import threading
import queue
import datetime
import time

# -------------------------
# Log Window
# -------------------------
class TextRedirector:
    def __init__(self, log_window):
        self.log_window = log_window

    def write(self, string):
        timestamp = datetime.datetime.now().strftime("[%H:%M:%S] ")
        self.log_window.queue.put(timestamp + string)

    def flush(self):
        pass

class LogWindow:
    def __init__(self, master):
        self.window = tk.Toplevel(master) if master else tb.Window(themename="cosmo")
        self.window.title("📢 Bot Logs")
        self.window.geometry("1000x600")
        self.clientData = None
        self.sections = {}
        # --- Main container (side by side panels) ---
        main_frame = tb.Frame(self.window)
        main_frame.pack(fill="both", expand=True, padx=10, pady=10)

        # --- Live Logs Panel ---
        live_panel = tb.Frame(main_frame)
        live_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 5))

        self.log_area_frame = tb.LabelFrame(live_panel, text="📡 Live Logs", padding=10)
        self.log_area_frame.pack(fill="both", expand=True)

        self.log_area = tk.Text(
            self.log_area_frame,
            wrap=tk.WORD,
            bg="#1e1e1e",
            fg="#00ff00",
            state="disabled",
        )
        self.log_area.pack(fill="both", expand=True)

        # --- Trading Bot Summary Panel ---
        summary_panel = tb.LabelFrame(
            main_frame, text="📜 Trading Bot Summary", padding=10
        )
        summary_panel.grid(row=0, column=1, sticky="nsew", padx=(5, 0))

        self.summary_area = tk.Text(
            summary_panel,
            wrap=tk.WORD,
            bg="#1e1e1e",
            fg="#00ff00",
            height=15,
            state="disabled",  # make readonly
        )
        self.summary_area.pack(fill="both", expand=True)

        # --- Load Sample Summary Data ---
        self._load_sample_summary()
        
        self.summary_container = tb.Frame(summary_panel)
        self.summary_container.pack(fill="both", expand=True, pady=10)

        self.add_collapsible_section("Change Rates", {
            "Daily": "+12%", 
            "Weekly": "+7%", 
            "Monthly": "-5%"
            })
        self.add_collapsible_section("Charts", {
            "Percentage Change": "+3.4%"})


        # --- Make columns resize proportionally ---
        main_frame.columnconfigure(0, weight=2)  # Live Logs wider
        main_frame.columnconfigure(1, weight=1)  # Summary smaller
        main_frame.rowconfigure(0, weight=1)

        # Stop button directly under Live Logs
        self.stop_button = tb.Button(
            live_panel, text="❌ Stop Bot", bootstyle=DANGER, command=self.quit
        )
        self.stop_button.pack(pady=5)

        # --- Queue + redirector ---
        self.queue = queue.Queue()
        self.poll_queue()
        self.redirector = TextRedirector(self)

    def _load_sample_summary(self):
        """Load sample trading bot summary data"""
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
        
        for key, value in summary_data.items():
            self.summary_area.insert(tk.END, f"{key}: {value}\n")
        self.summary_area.config(state="disabled")
        
    def add_collapsible_section(self, title, data: dict):
        """Add a collapsible panel to the summary container"""
        section_frame = tb.Frame(self.summary_container)
        section_frame.pack(fill="x", pady=5)

        # Button to toggle
        toggle_btn = tb.Button(section_frame, text=f"▶ {title}", bootstyle=INFO, width=20)
        toggle_btn.pack(fill="x")

        # Content frame
        content_frame = tb.Frame(section_frame)
        content_frame.pack(fill="x", padx=10, pady=2)
        content_frame.visible = True  # Custom attribute

        # Populate content
        for key, value in data.items():
            tb.Label(content_frame, text=f"{key}: {value}", anchor="w").pack(fill="x")

        # Toggle function
        def toggle():
            if content_frame.visible:
                content_frame.pack_forget()
                toggle_btn.config(text=f"▼ {title}")
            else:
                content_frame.pack(fill="x", padx=10, pady=2)
                toggle_btn.config(text=f"▶ {title}")
            content_frame.visible = not content_frame.visible

        toggle_btn.config(command=toggle)
        
    def poll_queue(self):
        try:
            while True:
                message = self.queue.get_nowait()
                self.log_area.insert(tk.END, message + "\n")
                self.log_area.see(tk.END)
        except queue.Empty:
            pass
        self.window.after(100, self.poll_queue)

    def quit(self):
        print("🛑 Stopping bot")
        UserGUI.should_run = False
        self.window.destroy()


# -------------------------
# Main GUI
# -------------------------
class UserGUI:
    should_run = False  # Class-level flag for bot state

    def __init__(self):
        self.root = tb.Window(themename="cosmo")
        self.root.title("🚀 Trading Bot Setup")
        self.root.geometry("800x600")

        self.user_data = {}
        self.log_window = None

        self.setup_gui()

    def setup_gui(self):
        print('Setting up GUI.....')
        main_frame = tb.Frame(self.root, padding=20)
        main_frame.pack(fill="both", expand=True)

        tb.Label(main_frame, text="Trading Bot Setup", font=("Segoe UI", 16, "bold")).pack(pady=(0, 15))

        # Trading setup fields
        trading_frame = tb.Labelframe(main_frame, text="⚙️ Trading Setup", padding=10)
        trading_frame.pack(fill="x", pady=10)

        self.volume = tb.Spinbox(trading_frame, from_=0.01, to=5.0, increment=0.01, width=10)
        self._add_field(trading_frame, "Volume", self.volume)

        self.sl = tb.Spinbox(trading_frame, from_=1, to=1000, increment=1, width=10)
        self._add_field(trading_frame, "SL Distance", self.sl)

        self.rr = tb.Combobox(trading_frame, values=["1:1", "1:2", "1:3", "1:5"], width=10)
        self.rr.current(2)
        self._add_field(trading_frame, "RR Ratio", self.rr)

        timeframes = tb.Labelframe(trading_frame, text="Timeframes", padding=5)
        timeframes.pack(fill="x", pady=5)
        self.tf_primary = tb.Combobox(timeframes, values=["1M", "5M", "15M", "30M", "1H", "4H", "1D", "1W", "1MN"], width=10)
        self.tf_primary.current(3)  # default 30M
        self.tf_primary.pack(side="left", padx=(0,5))

        self.tf_secondary = tb.Combobox(timeframes, values=["1M", "5M", "15M", "30M", "1H", "4H", "1D", "1W", "1MN"], width=10)
        self.tf_secondary.current(4)  # default 1H
        self.tf_secondary.pack(side="left", padx=(0,5))

        # Account info
        account_frame = tb.Labelframe(main_frame, text="👤 Account Info", padding=10)
        account_frame.pack(fill="x", pady=10)

        self.server = tb.Entry(account_frame, width=25)
        self._add_field(account_frame, "Server", self.server)

        self.account_id = tb.Entry(account_frame, width=25)
        self._add_field(account_frame, "Account ID", self.account_id)

        self.password = tb.Entry(account_frame, show="●", width=25)
        self._add_field(account_frame, "Password", self.password)

        # Buttons
        btn_frame = tb.Frame(main_frame)
        btn_frame.pack(pady=15)
        tb.Button(btn_frame, text="▶ Run Bot", bootstyle=SUCCESS, command=self.start_bot).pack(side="left", padx=10)
        tb.Button(btn_frame, text="❌ Stop Bot", bootstyle=DANGER, command=self.stop_bot).pack(side="left", padx=10)

        # Status
        self.status = tb.Label(self.root, text="Ready", bootstyle=INFO, anchor="w")
        self.status.pack(side="bottom", fill="x")

    def _add_field(self, parent, label, widget):
        frame = tb.Frame(parent)
        frame.pack(fill="x", pady=5)
        tb.Label(frame, text=label, width=15, anchor="e").pack(side="left", padx=5)
        widget.pack(side="left", padx=5)

    # -------------------------
    # Bot control
    # -------------------------
    def start_bot(self):
        # Validations
        if not self.account_id.get().isdigit():
            messagebox.showerror("Error", "Account ID must be numeric")
            return None
        if not self.server.get().strip() or not self.volume.get().strip():
            messagebox.showerror("Input Error", "Server and Volume are required.")
            return None
        if not self.account_id.get().strip().isdigit() or len(self.account_id.get().strip()) < 5:
            messagebox.showerror("Input Error", "Valid Account ID is required.")
            return None
        if not self.password.get().strip() or not (8 <= len(self.password.get().strip()) <= 16):
            messagebox.showerror("Input Error", "Password must be 8–16 characters.")
            return None


        self.user_data = {
            "volume": float(self.volume.get().strip()),
            "sl": int(self.sl.get().strip()),
            "rr": self.rr.get().strip(),
            "tf":{
                "LTF": self.tf_primary.get().strip(),
                "HTF": self.tf_secondary.get().strip()
            },
            "server": self.server.get().strip(),
            "account_id": self.account_id.get().strip(),
            "password": self.password.get().strip()
        }

        self.status.config(text="🚀 Bot Running...")
        self.root.withdraw()  # Hide main window
        UserGUI.should_run = True

        # # Redirect stdout/stderr to log window
        # import sys
        # sys.stdout = self.log_window.redirector
        # sys.stderr = self.log_window.redirector
        return self.user_data

    def stop_bot(self):
        UserGUI.should_run = False
        self.status.config(text="🛑 Bot Stopped")
        print("🛑 Bot stopped manually")

    def bot_worker(self):
        # Example bot logic
        for i in range(1, 21):
            if not UserGUI.should_run:
                print("🛑 Bot stopped")
                break
            # print(f"📈 Bot beat {i}")
            time.sleep(0.5)
        else:
            print("✅ Bot finished")
        self.status.config(text="Ready")

# # -------------------------
# # Run
# # -------------------------
# if __name__ == "__main__":
#     UserGUI()
