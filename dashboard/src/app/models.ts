export interface ProcessStatus {
  running: boolean;
  pid: number | null;
  restart_count: number;
  last_heartbeat: string | null;
  dependencies: string[];
}

export interface HealthEntry {
  status: string;
  meta: Record<string, unknown>;
  timestamp: string;
}

export interface TelemetryEntry {
  enabled: boolean;
  last_state_sync: string | null;
  last_data_fetch: string | null;
  last_signal_time: string | null;
  last_trade_time: string | null;
  last_error_time: string | null;
  last_error: string | null;
  data_fetch_count: number;
  signal_count: number;
  trade_count: number;
  error_count: number;
  meta: Record<string, unknown>;
}

export interface BotState {
  version: string;
  state: string;
  backtest_running: boolean;
  live_trading_enabled: boolean;
  last_backtest_run: string | null;
  next_backtest_run: string | null;
  symbols: Array<{
    symbol: string;
    enabled: boolean;
    score: number;
    last_backtest: string | null;
  }>;
}

export interface StatusResponse {
  health: Record<string, HealthEntry>;
  processes: Record<string, ProcessStatus>;
  telemetry: Record<string, TelemetryEntry>;
  bot_state: BotState;
  timestamp: string;
}

export interface AccountHistoryPoint {
  timestamp: string;
  equity: number;
  balance?: number;
}

export interface AccountHistorySummary {
  min: number;
  max: number;
  latest: number;
  change: number;
  change_pct: number;
  count: number;
}

export interface AccountSnapshot {
  login?: number;
  name?: string;
  server?: string;
  currency?: string;
  balance?: number;
  equity?: number;
  margin?: number;
  margin_free?: number;
  margin_level?: number;
  profit?: number;
  leverage?: number;
  max_open_trades?: number;
  max_daily_loss?: number;
  max_concurrent_trades?: number;
}

export interface AccountDailyActivity {
  date: string;
  profit: number;
  loss: number;
  net: number;
  trades: number;
}

export interface AccountCashflow {
  timestamp: string;
  date: string;
  amount: number;
  type: string;
  note: string;
}

export interface AccountActivitySummary {
  total_profit: number;
  total_loss: number;
  net: number;
  trades: number;
  deposits: number;
  withdrawals: number;
}

export interface AccountActivity {
  daily: AccountDailyActivity[];
  cashflows: AccountCashflow[];
  summary: AccountActivitySummary;
}

export interface AccountHistoryResponse {
  source: string;
  account?: AccountSnapshot;
  activity?: AccountActivity;
  points: AccountHistoryPoint[];
  summary: AccountHistorySummary;
}

export interface SupportTicketPayload {
  name?: string;
  email?: string;
  subject: string;
  message: string;
  priority?: string;
}

export interface SupportTicketResponse {
  ok: boolean;
  ticket_id: string;
}

export interface BacktestRequestPayload {
  symbol?: string;
  strategy_name?: string;
  strategy?: string;
}

export interface StrategyCreateRequestPayload {
  name?: string;
  config?: Record<string, unknown>;
  overwrite?: boolean;
}

export interface StrategyCreateResponse {
  ok: boolean;
  name: string;
  overwrite: boolean;
  config_path: string;
  config: Record<string, unknown>;
}

export interface StrategyToolOption {
  name: string;
  label: string;
  type: string;
  params: Record<string, unknown>;
}

export interface StrategyCatalogResponse {
  defaults: Record<string, unknown>;
  timeframes: string[];
  indicators: StrategyToolOption[];
  technical: StrategyToolOption[];
  patterns: StrategyToolOption[];
}

export interface StrategyRegistryStats {
  attach_count?: number;
  signal_count?: number;
  backtest_count?: number;
  pass_count?: number;
  fail_count?: number;
  error_count?: number;
  last_attach_at?: string | null;
  last_signal_at?: string | null;
  last_backtest_at?: string | null;
  last_backtest_ok?: boolean | null;
  last_error_at?: string | null;
  last_score?: number | null;
  last_confidence?: number | null;
}

export interface StrategyRegistryListItem {
  name: string;
  key: string;
  updated_at: string | null;
  stats: StrategyRegistryStats;
  symbols: string[];
}

export interface StrategyRegistryListResponse {
  strategies: StrategyRegistryListItem[];
}

export interface SupportArticle {
  id: string;
  title: string;
  summary: string;
  tag: string;
}

export interface SupportKbResponse {
  articles: SupportArticle[];
}
