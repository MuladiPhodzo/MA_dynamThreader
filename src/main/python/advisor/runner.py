import threading
import multiprocessing

from .process.process_engine import Supervisor
from .mt5_pipeline.runner import pipelineProcess
from .backtest.engine import backtestProcess
from .indicators import strategy
from .bootstrap.sys_bootstrap import SystemBootstrap
from .Trade import tradeHandler
from .GUI.userInput import UserGUI as setUpWizard
from .utils.dataHandler import CacheManager, dataHandler

class Ochestartor:
    def __init__(self):
        self.pl = None
        self.supervisor = Supervisor()
        self.sys_cfgs = self._config_variables()
        
        self.cache = CacheManager()
        self.main_stop_event = multiprocessing.Event()
        pass
    
    def _config_variables(self) -> dict:
        try:
            return SystemBootstrap.run()
        except Exception:
            pass
    
    def _init_pl_process(self):
        try:
            self.pl = pipelineProcess(self.sys_cfgs["user"], self.cache)
            self.supervisor.register_process(name="mt5_pipeline", target=self.pl.schedule_pipeline())
        except Exception:
            pass
        
    def _register_backtest(self):
        try:
            self.backtest = backtestProcess(self.pl.client, self.cache)
            self.supervisor.register_process("backtest", self.backtest.run_backtest_cycle())
        except Exception:
            pass
        
    def _register_strategy(self):
        try:
            self.strategy = strategy.strategyManager(dataHandler(symbol, strategy=))
        except Exception:
            pass
    # -------------------------
    # GUI Event Loop
    # -------------------------
    def create_dashboard(self):
        """Entry point for running the bot with GUI monitoring."""
        def start_when_ready():
            if self.gui.should_run:
                logger.info("🟢 Running bot...")
                threading.Thread(target=dashboard, daemon=True).start()
            else:
                self.gui.root.after(1000, start_when_ready)

        start_when_ready()
        self.gui.root.mainloop()
        
    def _main_orchestrator(self):
        # bootstrap configs
        cfgs = self._config_variables()
        if cfgs is None:
            cfgs = setUpWizard()
            
        self._init_pl_process()
        while not self.main_stop_event.is_set():
            try:
                self.supervisor._start_process("mt5_pipeline")
                if self.pl.done:
                    self.supervisor._start_process("backtest")
                self.supervisor.monitor()
                
                
            except Exception:
                pass
            finally:
                self.main_stop_event.set()
