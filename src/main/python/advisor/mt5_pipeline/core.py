import asyncio
from typing import Callable, Dict
from advisor.Client.mt5Client import MetaTrader5Client
from advisor.Client.symbols.symbol_watch import SymbolWatch
from advisor.utils.dataHandler import CacheManager
from advisor.utils.logging_setup import get_logger

logger = get_logger(__name__)


class MarketDataPipeline:
    """
    Stateless market data ingestion logic.
    """

    def __init__(self, client: MetaTrader5Client, cache_handler: CacheManager, symbol_watch: SymbolWatch):
        self.client = client
        self.cache = cache_handler
        self.symbol_watch = symbol_watch

    def fetch_symbol(self, symbol: str) -> Dict | None:
        try:
            data = self.client.get_multi_tf_data(symbol)
            if data is None:
                logger.warning(f"No data returned for {symbol}")
                self.symbol_watch.mark_error(symbol, "no data returned")
                return None
            return data
        except Exception:
            logger.exception(f"Failed fetching data for {symbol}")
            self.symbol_watch.mark_error(symbol, "fetch failed")
            return None

    async def ingest_symbol(self, symbol: str) -> dict | None:
        # Offload blocking MT5 call
        data = await asyncio.to_thread(self.fetch_symbol, symbol)
        if data is None:
            return None
        return data

    async def _ingest_with_timeout(self, symbol: str, timeout: float | None) -> dict | None:
        if timeout:
            return await asyncio.wait_for(self.ingest_symbol(symbol), timeout=timeout)
        return await self.ingest_symbol(symbol)

    async def run_once(
        self,
        on_symbol: Callable[[str, bool], None] | None = None,
        per_symbol_timeout: float | None = None,
        max_concurrent: int | None = None,
    ) -> None:
        symbols = self.symbol_watch.active_symbol_names() or self.symbol_watch.all_symbol_names()
        semaphore = asyncio.Semaphore(max_concurrent) if max_concurrent else None

        async def _wrap(symbol: str):
            try:
                if semaphore:
                    async with semaphore:
                        data = await self._ingest_with_timeout(symbol, per_symbol_timeout)
                else:
                    data = await self._ingest_with_timeout(symbol, per_symbol_timeout)
                return symbol, data, None
            except Exception as exc:
                return symbol, None, exc

        tasks = [asyncio.create_task(_wrap(symbol)) for symbol in symbols]

        for task in asyncio.as_completed(tasks):
            symbol, data, err = await task
            if err is not None:
                if isinstance(err, asyncio.TimeoutError):
                    logger.warning("Timeout fetching data for %s", symbol)
                else:
                    logger.error("Failed fetching data for %s: %s", symbol, err)
                self.symbol_watch.mark_error(symbol, "fetch failed")
                if on_symbol is not None:
                    on_symbol(symbol, False)
                continue

            if data is None:
                if on_symbol is not None:
                    on_symbol(symbol, False)
                continue

            self.cache.set_atomic(symbol, data)
            self.symbol_watch.mark_data_fetch(symbol)
            if on_symbol is not None:
                on_symbol(symbol, True)
