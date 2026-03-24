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

export interface AccountHistoryResponse {
  source: string;
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

export interface SupportArticle {
  id: string;
  title: string;
  summary: string;
  tag: string;
}

export interface SupportKbResponse {
  articles: SupportArticle[];
}
