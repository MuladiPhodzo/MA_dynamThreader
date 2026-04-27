import { CommonModule } from "@angular/common";
import { Component, OnDestroy, OnInit } from "@angular/core";
import { FormsModule } from "@angular/forms";
import { Subscription, timer, firstValueFrom } from "rxjs";
import { switchMap } from "rxjs/operators";
import { ApiService } from "./api.service";
import {
  BacktestRequestPayload,
  AccountHistoryPoint,
  AccountHistoryResponse,
  AccountHistorySummary,
  AccountSnapshot,
  AccountDailyActivity,
  AccountCashflow,
  StatusResponse,
  SupportArticle,
  StrategyRegistryListItem,
  StrategyRegistryListResponse,
  StrategyCreateResponse,
  StrategyCatalogResponse,
  StrategyToolOption,
  SupportTicketPayload,
} from "./models";

type PanelKey = "account" | "symbols" | "strategy" | "bot" | "logs" | "store" | "support";

type SymbolFilter = "all" | "active" | "inactive";

type LogSeverity = "all" | "critical" | "warn" | "info";

type ToolCategory = "indicators" | "technical" | "patterns";

interface ParamEntry {
  key: string;
  value: unknown;
  type: "boolean" | "number" | "text";
}

interface IssueEntry {
  severity: "critical" | "warn" | "info";
  title: string;
  detail: string;
  time: string | null;
}

interface StrategyFormState {
  name: string;
  htf: string;
  trend: string;
  bias: string;
  entry: string;
  indicatorsEnabled: boolean;
  selectedIndicators: Record<string, boolean>;
  indicatorParams: Record<string, Record<string, unknown>>;
  technicalEnabled: boolean;
  selectedTechnical: Record<string, boolean>;
  technicalParams: Record<string, unknown>;
  patternsEnabled: boolean;
  selectedPatterns: Record<string, boolean>;
  patternParams: Record<string, unknown>;
  confluenceEnabled: boolean;
  requireAlignment: boolean;
  minAlignment: number;
  minTrendStrength: number;
  requireBiasEngulfing: boolean;
  requireEntryTrigger: boolean;
  requireMomentumConfirmation: boolean;
  requireSmcContext: boolean;
  liveEnabled: boolean;
  liveBufferSize: number;
  minScore: number;
  minConfidence: number;
  entryFiltersEnabled: boolean;
  proximity: number;
  levels: number;
  requireMtfAlignment: boolean;
  requireMomentum: boolean;
  requireSmcConfluence: boolean;
  highFrequencyEnabled: boolean;
  maxTrades: number;
  slAtrMult: number;
  tpAtrMult: number;
}

@Component({
  selector: "app-root",
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: "./app.component.html",
  styleUrls: ["./app.component.css"],
})
export class AppComponent implements OnInit, OnDestroy {
  private readonly backtestStorageKey = "movingaverage.dashboard.backtest";
  private readonly strategyFormStorageKey = "movingaverage.dashboard.strategyForm";
  status: StatusResponse | null = null;
  lastUpdated: string | null = null;
  loading = true;
  error: string | null = null;
  activePanel: PanelKey = "account";
  issues: IssueEntry[] = [];
  filteredSymbolsList: string[] = [];
  filteredIssuesList: IssueEntry[] = [];
  symbolQuery = "";
  symbolFilter: SymbolFilter = "all";
  logQuery = "";
  logSeverity: LogSeverity = "all";

  accountHistory: AccountHistoryResponse | null = null;
  accountSummary: AccountHistorySummary | null = null;
  accountSnapshot: AccountSnapshot | null = null;
  accountDailyActivity: AccountDailyActivity[] = [];
  accountCashflows: AccountCashflow[] = [];
  equitySeries: AccountHistoryPoint[] = [];
  recentSnapshots: AccountHistoryPoint[] = [];
  strategyRegistry: StrategyRegistryListItem[] = [];
  equityPolyline = "";
  equityAreaPath = "";
  equityLatest = 0;
  equityChange = 0;
  equityChangePct = 0;

  supportForm: SupportTicketPayload = {
    name: "",
    email: "",
    subject: "",
    message: "",
    priority: "normal",
  };
  supportStatus: string | null = null;
  supportError: string | null = null;
  supportSubmitting = false;
  kbArticles: SupportArticle[] = [];
  kbLoading = false;
  actionStatus: string | null = null;
  actionError: string | null = null;
  backtestSymbol = "";
  backtestStrategy = "";
  strategyCatalog: StrategyCatalogResponse | null = null;
  strategyCatalogError: string | null = null;
  strategyForm: StrategyFormState = this.defaultStrategyForm();
  strategyPreview = "";

  private statusSub: Subscription | null = null;
  private historySub: Subscription | null = null;
  private strategySub: Subscription | null = null;

  constructor(private api: ApiService) {}

  private defaultStrategyForm(): StrategyFormState {
    return {
      name: "EMA_Proxim8te",
      htf: "1D",
      trend: "4H",
      bias: "1H",
      entry: "15M",
      indicatorsEnabled: true,
      selectedIndicators: {},
      indicatorParams: {},
      technicalEnabled: true,
      selectedTechnical: {},
      technicalParams: {},
      patternsEnabled: true,
      selectedPatterns: {},
      patternParams: {},
      confluenceEnabled: true,
      requireAlignment: true,
      minAlignment: 0.67,
      minTrendStrength: 0.5,
      requireBiasEngulfing: true,
      requireEntryTrigger: true,
      requireMomentumConfirmation: true,
      requireSmcContext: true,
      liveEnabled: false,
      liveBufferSize: 250,
      minScore: 0.65,
      minConfidence: 50,
      entryFiltersEnabled: true,
      proximity: 200,
      levels: 10,
      requireMtfAlignment: true,
      requireMomentum: true,
      requireSmcConfluence: true,
      highFrequencyEnabled: true,
      maxTrades: 5,
      slAtrMult: 1.5,
      tpAtrMult: 3.0,
    };
  }

  ngOnInit(): void {
    this.loadBacktestSelection();
    this.loadStrategyForm();
    this.loadStrategyCatalog();
    this.compileStrategyPreview();
    this.statusSub = timer(0, 2000)
      .pipe(switchMap(() => this.api.getStatus()))
      .subscribe({
        next: (data) => {
          this.status = data;
          this.lastUpdated = data.timestamp;
          this.loading = false;
          this.error = null;
          this.updateIssues();
          this.refreshFilteredSymbols();
          this.syncBacktestDefaults();
        },
        error: (err) => {
          this.loading = false;
          this.error = err?.message || "Failed to load status";
          this.updateIssues();
        },
      });

    this.historySub = timer(0, 10000)
      .pipe(switchMap(() => this.api.getAccountHistory()))
      .subscribe({
        next: (data) => {
          this.updateAccountHistory(data);
        },
        error: () => {
          this.accountHistory = null;
          this.accountSummary = null;
          this.accountSnapshot = null;
          this.accountDailyActivity = [];
          this.accountCashflows = [];
          this.equitySeries = [];
          this.recentSnapshots = [];
          this.equityPolyline = "";
          this.equityAreaPath = "";
        },
      });

    this.strategySub = timer(0, 10000)
      .pipe(switchMap(() => this.api.getStrategyList()))
      .subscribe({
        next: (data) => {
          this.updateStrategyRegistry(data);
        },
        error: () => {
          this.strategyRegistry = [];
        },
      });
  }

  ngOnDestroy(): void {
    this.statusSub?.unsubscribe();
    this.historySub?.unsubscribe();
    this.strategySub?.unsubscribe();
  }

  processNames(): string[] {
    if (!this.status) return [];
    return Object.keys(this.status.processes);
  }

  symbolNames(): string[] {
    if (!this.status) return [];
    return Object.keys(this.status.telemetry);
  }

  refreshFilteredSymbols() {
    if (!this.status) {
      this.filteredSymbolsList = [];
      return;
    }
    const base = this.symbolNames();
    const query = this.symbolQuery.trim().toLowerCase();
    this.filteredSymbolsList = base.filter((symbol) => {
      const entry = this.status?.telemetry[symbol];
      if (this.symbolFilter === "active" && !entry?.enabled) return false;
      if (this.symbolFilter === "inactive" && entry?.enabled) return false;
      if (!query) return true;
      return symbol.toLowerCase().includes(query);
    });
  }

  private syncBacktestDefaults() {
    const symbols = this.symbolNames();
    if (!symbols.length) {
      this.backtestSymbol = "";
      this.persistBacktestSelection();
      return;
    }

    if (!this.backtestSymbol || !symbols.includes(this.backtestSymbol)) {
      this.backtestSymbol = symbols[0];
      this.persistBacktestSelection();
    }
  }

  private loadBacktestSelection() {
    try {
      const raw = window.localStorage.getItem(this.backtestStorageKey);
      if (!raw) return;
      const parsed = JSON.parse(raw) as Partial<{ symbol: string; strategy: string }>;
      if (parsed?.symbol) {
        this.backtestSymbol = parsed.symbol;
      }
      if (parsed?.strategy) {
        this.backtestStrategy = parsed.strategy;
      }
    } catch {
      // Ignore malformed or unavailable local storage.
    }
  }

  persistBacktestSelection() {
    try {
      window.localStorage.setItem(
        this.backtestStorageKey,
        JSON.stringify({
          symbol: this.backtestSymbol,
          strategy: this.backtestStrategy,
        })
      );
    } catch {
      // Ignore storage failures in restricted environments.
    }
  }

  private loadStrategyForm() {
    try {
      const raw = window.localStorage.getItem(this.strategyFormStorageKey);
      if (!raw) return;
      const parsed = JSON.parse(raw) as Partial<StrategyFormState>;
      this.strategyForm = this.normalizeStrategyForm({ ...this.defaultStrategyForm(), ...parsed });
    } catch {
      this.strategyForm = this.defaultStrategyForm();
    }
  }

  private async loadStrategyCatalog() {
    try {
      this.strategyCatalog = await firstValueFrom(this.api.getStrategyCatalog());
      this.strategyCatalogError = null;
      this.applyStrategyCatalogDefaults(false);
      this.compileStrategyPreview();
    } catch (err: any) {
      this.strategyCatalogError = err?.message || "Failed to load strategy tool catalog.";
    }
  }

  persistStrategyForm() {
    try {
      window.localStorage.setItem(this.strategyFormStorageKey, JSON.stringify(this.strategyForm));
    } catch {
      // Ignore storage failures in restricted environments.
    }
  }

  resetStrategyForm() {
    this.strategyForm = this.defaultStrategyForm();
    this.applyStrategyCatalogDefaults(true);
    this.persistStrategyForm();
    this.compileStrategyPreview();
  }

  private toNumber(value: unknown, fallback: number): number {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  private asRecord(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : {};
  }

  private asParamMap(value: unknown): Record<string, Record<string, unknown>> {
    const source = this.asRecord(value);
    return Object.entries(source).reduce<Record<string, Record<string, unknown>>>((acc, [key, val]) => {
      acc[key] = { ...this.asRecord(val) };
      return acc;
    }, {});
  }

  private asSelection(value: unknown): Record<string, boolean> {
    const source = this.asRecord(value);
    return Object.entries(source).reduce<Record<string, boolean>>((acc, [key, val]) => {
      acc[key] = Boolean(val);
      return acc;
    }, {});
  }

  private normalizeStrategyForm(form: StrategyFormState): StrategyFormState {
    return {
      ...form,
      selectedIndicators: this.asSelection(form.selectedIndicators),
      indicatorParams: this.asParamMap(form.indicatorParams),
      selectedTechnical: this.asSelection(form.selectedTechnical),
      technicalParams: { ...this.asRecord(form.technicalParams) },
      selectedPatterns: this.asSelection(form.selectedPatterns),
      patternParams: { ...this.asRecord(form.patternParams) },
    };
  }

  private toStringList(value: unknown): string[] {
    if (Array.isArray(value)) {
      return value.map((item) => String(item).trim()).filter((item) => !!item);
    }
    if (typeof value === "string") {
      return value.split(",").map((item) => item.trim()).filter((item) => !!item);
    }
    return [];
  }

  private selectionFromList(values: string[]): Record<string, boolean> {
    return values.reduce<Record<string, boolean>>((acc, item) => {
      acc[item] = true;
      return acc;
    }, {});
  }

  private cloneParamRecord(value: unknown): Record<string, unknown> {
    return { ...this.asRecord(value) };
  }

  private cloneNestedParams(value: unknown): Record<string, Record<string, unknown>> {
    return this.asParamMap(value);
  }

  private hasAnySelectedTool(): boolean {
    return (
      this.selectedToolNames("indicators").length > 0 ||
      this.selectedToolNames("technical").length > 0 ||
      this.selectedToolNames("patterns").length > 0
    );
  }

  private applyStrategyCatalogDefaults(force: boolean) {
    if (!this.strategyCatalog) return;
    const defaults = this.strategyCatalog.defaults || {};
    const timeframes = this.asRecord(defaults["timeframes"]);
    const tools = this.asRecord(defaults["tools"]);
    const shouldLoadDefaults = force || !this.hasAnySelectedTool();

    if (shouldLoadDefaults) {
      this.strategyForm.name = String(defaults["name"] || this.strategyForm.name || "EMA_Proxim8te");
      this.strategyForm.htf = String(timeframes["HTF"] || this.strategyForm.htf || "1D");
      this.strategyForm.trend = String(timeframes["TREND"] || this.strategyForm.trend || "4H");
      this.strategyForm.bias = String(timeframes["BIAS"] || this.strategyForm.bias || "1H");
      this.strategyForm.entry = String(timeframes["ENTRY"] || this.strategyForm.entry || "15M");

      const indicators = this.asRecord(tools["indicators"]);
      const indicatorParams = this.asRecord(indicators["params"]);
      this.strategyForm.indicatorsEnabled = Boolean(indicators["enabled"] ?? true);
      this.strategyForm.selectedIndicators = this.selectionFromList(Object.keys(indicatorParams));
      this.strategyForm.indicatorParams = this.cloneNestedParams(indicatorParams);

      const technical = this.asRecord(tools["technical"]);
      this.strategyForm.technicalEnabled = Boolean(technical["enabled"] ?? true);
      this.strategyForm.selectedTechnical = this.selectionFromList(this.toStringList(technical["tools"]));
      this.strategyForm.technicalParams = this.cloneParamRecord(technical["params"]);

      const patterns = this.asRecord(tools["patterns"]);
      this.strategyForm.patternsEnabled = Boolean(patterns["enabled"] ?? true);
      this.strategyForm.selectedPatterns = this.selectionFromList(this.toStringList(patterns["patterns"]));
      this.strategyForm.patternParams = this.cloneParamRecord(patterns["params"]);

      const confluence = this.asRecord(defaults["confluence"]);
      const gates = this.asRecord(confluence["gates"]);
      this.strategyForm.confluenceEnabled = Boolean(confluence["enabled"] ?? this.strategyForm.confluenceEnabled);
      this.strategyForm.requireAlignment = Boolean(gates["require_alignment"] ?? this.strategyForm.requireAlignment);
      this.strategyForm.minAlignment = this.toNumber(gates["min_alignment"], this.strategyForm.minAlignment);
      this.strategyForm.minTrendStrength = this.toNumber(gates["min_trend_strength"], this.strategyForm.minTrendStrength);
      this.strategyForm.requireBiasEngulfing = Boolean(gates["require_bias_engulfing"] ?? this.strategyForm.requireBiasEngulfing);
      this.strategyForm.requireEntryTrigger = Boolean(gates["require_entry_trigger"] ?? this.strategyForm.requireEntryTrigger);
      this.strategyForm.requireMomentumConfirmation = Boolean(
        gates["require_momentum_confirmation"] ?? this.strategyForm.requireMomentumConfirmation
      );
      this.strategyForm.requireSmcContext = Boolean(gates["require_smc_context"] ?? this.strategyForm.requireSmcContext);

      const live = this.asRecord(defaults["live"]);
      this.strategyForm.liveEnabled = Boolean(live["enabled"] ?? this.strategyForm.liveEnabled);
      this.strategyForm.liveBufferSize = this.toNumber(live["buffer_size"], this.strategyForm.liveBufferSize);

      const rules = this.asRecord(defaults["rules"]);
      const entryFilters = this.asRecord(rules["entry_filters"]);
      this.strategyForm.minScore = this.toNumber(rules["min_score"], this.strategyForm.minScore);
      this.strategyForm.minConfidence = this.toNumber(rules["min_confidence"], this.strategyForm.minConfidence);
      this.strategyForm.entryFiltersEnabled = Boolean(entryFilters["enabled"] ?? this.strategyForm.entryFiltersEnabled);
      this.strategyForm.proximity = this.toNumber(entryFilters["proximity"], this.strategyForm.proximity);
      this.strategyForm.levels = this.toNumber(entryFilters["levels"], this.strategyForm.levels);
      this.strategyForm.requireMtfAlignment = Boolean(rules["require_mtf_alignment"] ?? this.strategyForm.requireMtfAlignment);
      this.strategyForm.requireMomentum = Boolean(rules["require_momentum"] ?? this.strategyForm.requireMomentum);
      this.strategyForm.requireSmcConfluence = Boolean(rules["require_smc_confluence"] ?? this.strategyForm.requireSmcConfluence);

      const risk = this.asRecord(defaults["risk"]);
      const highFrequency = this.asRecord(risk["high_frequency_trade"]);
      this.strategyForm.highFrequencyEnabled = Boolean(highFrequency["enabled"] ?? this.strategyForm.highFrequencyEnabled);
      this.strategyForm.maxTrades = this.toNumber(highFrequency["max_trades"], this.strategyForm.maxTrades);
      this.strategyForm.slAtrMult = this.toNumber(risk["sl_atr_mult"], this.strategyForm.slAtrMult);
      this.strategyForm.tpAtrMult = this.toNumber(risk["tp_atr_mult"], this.strategyForm.tpAtrMult);
    }

    this.ensureCatalogToolState();
  }

  private ensureCatalogToolState() {
    for (const tool of this.toolOptions("indicators")) {
      if (this.strategyForm.selectedIndicators[tool.name] === undefined) {
        this.strategyForm.selectedIndicators[tool.name] = false;
      }
      this.strategyForm.indicatorParams[tool.name] = {
        ...(tool.params || {}),
        ...(this.strategyForm.indicatorParams[tool.name] || {}),
      };
    }

    for (const tool of this.toolOptions("technical")) {
      if (this.strategyForm.selectedTechnical[tool.name] === undefined) {
        this.strategyForm.selectedTechnical[tool.name] = false;
      }
      if (!Object.keys(this.strategyForm.technicalParams).length) {
        this.strategyForm.technicalParams = { ...(tool.params || {}) };
      }
    }

    for (const tool of this.toolOptions("patterns")) {
      if (this.strategyForm.selectedPatterns[tool.name] === undefined) {
        this.strategyForm.selectedPatterns[tool.name] = false;
      }
      if (!Object.keys(this.strategyForm.patternParams).length) {
        this.strategyForm.patternParams = { ...(tool.params || {}) };
      }
    }
  }

  toolOptions(category: ToolCategory): StrategyToolOption[] {
    if (!this.strategyCatalog) return [];
    return this.strategyCatalog[category] || [];
  }

  private selectionFor(category: ToolCategory): Record<string, boolean> {
    if (category === "indicators") return this.strategyForm.selectedIndicators;
    if (category === "technical") return this.strategyForm.selectedTechnical;
    return this.strategyForm.selectedPatterns;
  }

  isToolSelected(category: ToolCategory, name: string): boolean {
    return Boolean(this.selectionFor(category)[name]);
  }

  setToolSelected(category: ToolCategory, name: string, selected: boolean) {
    this.selectionFor(category)[name] = selected;
    if (category === "indicators") {
      const tool = this.toolOptions("indicators").find((item) => item.name === name);
      this.strategyForm.indicatorParams[name] = {
        ...(tool?.params || {}),
        ...(this.strategyForm.indicatorParams[name] || {}),
      };
    }
    this.compileStrategyPreview();
  }

  selectedToolNames(category: ToolCategory): string[] {
    const selection = this.selectionFor(category);
    const options = this.toolOptions(category);
    if (options.length) {
      return options.filter((tool) => selection[tool.name]).map((tool) => tool.name);
    }
    return Object.entries(selection)
      .filter(([, selected]) => selected)
      .map(([name]) => name);
  }

  labelize(value: string): string {
    return String(value || "")
      .replace(/[_-]+/g, " ")
      .replace(/\b\w/g, (char) => char.toUpperCase());
  }

  private paramType(value: unknown): ParamEntry["type"] {
    if (typeof value === "boolean") return "boolean";
    if (typeof value === "number") return "number";
    return "text";
  }

  private paramEntries(params: Record<string, unknown>): ParamEntry[] {
    return Object.entries(params || {}).map(([key, value]) => ({
      key,
      value,
      type: this.paramType(value),
    }));
  }

  indicatorParamEntries(tool: StrategyToolOption): ParamEntry[] {
    return this.paramEntries(this.strategyForm.indicatorParams[tool.name] || tool.params || {});
  }

  technicalParamEntries(): ParamEntry[] {
    return this.paramEntries(this.strategyForm.technicalParams);
  }

  patternParamEntries(): ParamEntry[] {
    return this.paramEntries(this.strategyForm.patternParams);
  }

  getIndicatorParam(toolName: string, key: string): unknown {
    return this.strategyForm.indicatorParams[toolName]?.[key];
  }

  setIndicatorParam(toolName: string, key: string, value: unknown) {
    if (!this.strategyForm.indicatorParams[toolName]) {
      this.strategyForm.indicatorParams[toolName] = {};
    }
    this.strategyForm.indicatorParams[toolName][key] = value;
    this.compileStrategyPreview();
  }

  setTechnicalParam(key: string, value: unknown) {
    this.strategyForm.technicalParams[key] = value;
    this.compileStrategyPreview();
  }

  setPatternParam(key: string, value: unknown) {
    this.strategyForm.patternParams[key] = value;
    this.compileStrategyPreview();
  }

  private normalizeParamValue(value: unknown): unknown {
    if (typeof value !== "string") return value;
    const trimmed = value.trim();
    if (!trimmed) return value;
    if (trimmed.toLowerCase() === "true") return true;
    if (trimmed.toLowerCase() === "false") return false;
    const numeric = Number(trimmed);
    return Number.isFinite(numeric) ? numeric : value;
  }

  private normalizeParamMap(params: Record<string, unknown>): Record<string, unknown> {
    return Object.entries(params || {}).reduce<Record<string, unknown>>((acc, [key, value]) => {
      acc[key] = this.normalizeParamValue(value);
      return acc;
    }, {});
  }

  private buildStrategyConfig(): Record<string, unknown> {
    const name = (this.strategyForm.name || "").trim() || "EMA_Proxim8te";
    const indicatorParams = this.selectedToolNames("indicators").reduce<Record<string, unknown>>((acc, toolName) => {
      acc[toolName] = this.normalizeParamMap(this.strategyForm.indicatorParams[toolName] || {});
      return acc;
    }, {});

    return {
      name,
      timeframes: {
        HTF: this.strategyForm.htf,
        TREND: this.strategyForm.trend,
        BIAS: this.strategyForm.bias,
        ENTRY: this.strategyForm.entry,
      },
      tools: {
        indicators: {
          enabled: this.strategyForm.indicatorsEnabled,
          params: indicatorParams,
        },
        technical: {
          enabled: this.strategyForm.technicalEnabled,
          tools: this.selectedToolNames("technical"),
          params: this.normalizeParamMap(this.strategyForm.technicalParams),
        },
        patterns: {
          enabled: this.strategyForm.patternsEnabled,
          patterns: this.selectedToolNames("patterns"),
          params: this.normalizeParamMap(this.strategyForm.patternParams),
        },
      },
      confluence: {
        enabled: this.strategyForm.confluenceEnabled,
        gates: {
          require_alignment: this.strategyForm.requireAlignment,
          min_alignment: this.toNumber(this.strategyForm.minAlignment, 0.67),
          min_trend_strength: this.toNumber(this.strategyForm.minTrendStrength, 0.5),
          require_bias_engulfing: this.strategyForm.requireBiasEngulfing,
          require_entry_trigger: this.strategyForm.requireEntryTrigger,
          require_momentum_confirmation: this.strategyForm.requireMomentumConfirmation,
          require_smc_context: this.strategyForm.requireSmcContext,
        },
      },
      live: {
        enabled: this.strategyForm.liveEnabled,
        buffer_size: this.toNumber(this.strategyForm.liveBufferSize, 250),
      },
      rules: {
        min_score: this.toNumber(this.strategyForm.minScore, 0.65),
        min_confidence: this.toNumber(this.strategyForm.minConfidence, 50),
        entry_filters: {
          enabled: this.strategyForm.entryFiltersEnabled,
          proximity: this.toNumber(this.strategyForm.proximity, 200),
          levels: this.toNumber(this.strategyForm.levels, 10),
        },
        require_mtf_alignment: this.strategyForm.requireMtfAlignment,
        require_momentum: this.strategyForm.requireMomentum,
        require_smc_confluence: this.strategyForm.requireSmcConfluence,
      },
      risk: {
        high_frequency_trade: {
          enabled: this.strategyForm.highFrequencyEnabled,
          max_trades: this.toNumber(this.strategyForm.maxTrades, 5),
        },
        sl_atr_mult: this.toNumber(this.strategyForm.slAtrMult, 1.5),
        tp_atr_mult: this.toNumber(this.strategyForm.tpAtrMult, 3.0),
      },
    };
  }

  compileStrategyPreview() {
    this.persistStrategyForm();
    const config = this.buildStrategyConfig();
    this.strategyPreview = JSON.stringify(config, null, 2);
  }

  activeSymbolCount(): number {
    if (!this.status) return 0;
    return Object.values(this.status.telemetry).filter((entry) => entry.enabled).length;
  }

  setActivePanel(panel: PanelKey) {
    this.activePanel = panel;
  }

  get panelMeta(): { title: string; subtitle: string } {
    switch (this.activePanel) {
      case "account":
        return {
          title: "Account History",
          subtitle: "Historical account snapshots and performance timeline.",
        };
      case "symbols":
        return {
          title: "Active Symbols",
          subtitle: "Live symbol telemetry with enable/disable controls.",
        };
      case "strategy":
        return {
          title: "Strategy Builder",
          subtitle: "Compile and save strategy configs in the same shape as StrategyModel.DEFAULT_CONFIG.",
        };
      case "bot":
        return {
          title: "Bot Control",
          subtitle: "Process health checks and runtime controls.",
        };
      case "logs":
        return {
          title: "Issue Logs",
          subtitle: "Warnings, errors, and health alerts.",
        };
      case "store":
        return {
          title: "Store",
          subtitle: "Coming soon.",
        };
      case "support":
        return {
          title: "Support",
          subtitle: "Get help and reach the operator support team.",
        };
      default:
        return {
          title: "Dashboard",
          subtitle: "Control center overview.",
        };
    }
  }

  refreshFilteredIssues() {
    const query = this.logQuery.trim().toLowerCase();
    this.filteredIssuesList = this.issues.filter((issue) => {
      if (this.logSeverity !== "all" && issue.severity !== this.logSeverity) return false;
      if (!query) return true;
      return (
        issue.title.toLowerCase().includes(query) ||
        issue.detail.toLowerCase().includes(query)
      );
    });
  }

  private updateIssues() {
    this.issues = this.buildIssues();
    this.refreshFilteredIssues();
  }

  private buildIssues(): IssueEntry[] {
    const issues: IssueEntry[] = [];

    if (this.error) {
      issues.push({
        severity: "critical",
        title: "API disconnected",
        detail: this.error,
        time: this.lastUpdated,
      });
    }

    if (!this.status) return issues;

    for (const [name, proc] of Object.entries(this.status.processes)) {
      if (!proc.running) {
        issues.push({
          severity: "critical",
          title: `Process stopped: ${name}`,
          detail: `PID ${proc.pid ?? "n/a"} | Restarts ${proc.restart_count}`,
          time: proc.last_heartbeat,
        });
      }
    }

    for (const [name, health] of Object.entries(this.status.health)) {
      if (health.status && health.status.toUpperCase() !== "OK") {
        issues.push({
          severity: "warn",
          title: `Health warning: ${name}`,
          detail: `${health.status}`,
          time: health.timestamp,
        });
      }
    }

    for (const [symbol, entry] of Object.entries(this.status.telemetry)) {
      if (entry.error_count > 0 || entry.last_error) {
        issues.push({
          severity: "warn",
          title: `Symbol errors: ${symbol}`,
          detail: `${entry.error_count} errors. ${entry.last_error || "See logs for details."}`,
          time: entry.last_error_time,
        });
      }
    }

    return issues;
  }

  private updateAccountHistory(data: AccountHistoryResponse) {
    this.accountHistory = data;
    this.accountSummary = data.summary ?? null;
    this.accountSnapshot = data.account ?? null;
    this.accountDailyActivity = data.activity?.daily || [];
    this.accountCashflows = data.activity?.cashflows || [];
    this.equitySeries = data.points || [];
    this.recentSnapshots = this.equitySeries.slice(-5);
    this.buildEquityChart();
  }

  accountActivityNet(): number {
    return this.accountHistory?.activity?.summary?.net || 0;
  }

  accountActivityTrades(): number {
    return this.accountHistory?.activity?.summary?.trades || 0;
  }

  accountDeposits(): number {
    return this.accountHistory?.activity?.summary?.deposits || 0;
  }

  accountWithdrawals(): number {
    return this.accountHistory?.activity?.summary?.withdrawals || 0;
  }

  activityScale(): number {
    const maxPnl = Math.max(
      ...this.accountDailyActivity.map((item) => Math.max(Math.abs(item.profit || 0), Math.abs(item.loss || 0))),
      1
    );
    return maxPnl;
  }

  activityBarWidth(value: number): string {
    const pct = Math.max(4, Math.min(100, (Math.abs(value || 0) / this.activityScale()) * 100));
    return `${pct}%`;
  }

  recentDailyActivity(): AccountDailyActivity[] {
    return this.accountDailyActivity.slice(-14).reverse();
  }

  private updateStrategyRegistry(data: StrategyRegistryListResponse) {
    this.strategyRegistry = (data?.strategies || []).slice();
  }

  strategyCount(): number {
    return this.strategyRegistry.length;
  }

  strategySignalCount(): number {
    return this.strategyRegistry.reduce((sum, item) => sum + Number(item?.stats?.signal_count || 0), 0);
  }

  strategyBacktestCount(): number {
    return this.strategyRegistry.reduce((sum, item) => sum + Number(item?.stats?.backtest_count || 0), 0);
  }

  strategyPassRate(): number {
    const pass = this.strategyRegistry.reduce((sum, item) => sum + Number(item?.stats?.pass_count || 0), 0);
    const fail = this.strategyRegistry.reduce((sum, item) => sum + Number(item?.stats?.fail_count || 0), 0);
    const total = pass + fail;
    if (!total) return 0;
    return (pass / total) * 100;
  }

  private buildEquityChart() {
    if (!this.equitySeries.length) {
      this.equityPolyline = "";
      this.equityAreaPath = "";
      this.equityLatest = 0;
      this.equityChange = 0;
      this.equityChangePct = 0;
      return;
    }

    const values = this.equitySeries.map((pt) => pt.equity);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = Math.max(max - min, 1);
    const first = values[0];
    const latest = values[values.length - 1];

    this.equityLatest = latest;
    this.equityChange = latest - first;
    this.equityChangePct = first !== 0 ? (this.equityChange / first) * 100 : 0;

    const width = 240;
    const height = 80;
    const pad = 6;
    const steps = Math.max(this.equitySeries.length - 1, 1);

    let coords = this.equitySeries.map((pt, index) => {
      const x = pad + (index / steps) * (width - pad * 2);
      const y = height - pad - ((pt.equity - min) / span) * (height - pad * 2);
      return { x, y };
    });

    if (coords.length === 1) {
      coords = [
        { x: pad, y: coords[0].y },
        { x: width - pad, y: coords[0].y },
      ];
    }

    this.equityPolyline = coords.map((pt) => `${pt.x},${pt.y}`).join(" ");

    const firstPt = coords[0];
    const lastPt = coords[coords.length - 1];
    const pathParts = coords.map((pt) => `L ${pt.x},${pt.y}`);
    pathParts[0] = `M ${firstPt.x},${firstPt.y}`;
    this.equityAreaPath = `${pathParts.join(" ")} L ${lastPt.x},${
      height - pad
    } L ${firstPt.x},${height - pad} Z`;
  }

  private async runAction(action: () => Promise<unknown>, successMessage: string) {
    this.actionError = null;
    this.actionStatus = null;
    try {
      await action();
      this.actionStatus = successMessage;
    } catch (err: any) {
      this.actionError = err?.message || "Action failed.";
    }
  }

  async submitSupportTicket() {
    if (!this.supportForm.subject || !this.supportForm.message) {
      this.supportError = "Subject and message are required.";
      return;
    }
    this.supportSubmitting = true;
    this.supportError = null;
    this.supportStatus = null;
    try {
      const res = await firstValueFrom(this.api.createSupportTicket(this.supportForm));
      this.supportStatus = `Ticket created: ${res.ticket_id}`;
      this.supportForm = {
        name: this.supportForm.name,
        email: this.supportForm.email,
        subject: "",
        message: "",
        priority: this.supportForm.priority,
      };
    } catch (err: any) {
      this.supportError = err?.message || "Failed to submit support ticket.";
    } finally {
      this.supportSubmitting = false;
    }
  }

  async loadSupportKb() {
    this.kbLoading = true;
    this.supportError = null;
    try {
      const res = await firstValueFrom(this.api.getSupportKb());
      this.kbArticles = res.articles || [];
    } catch (err: any) {
      this.supportError = err?.message || "Failed to load knowledge base.";
    } finally {
      this.kbLoading = false;
    }
  }

  async start(name: string) {
    await this.runAction(
      () => firstValueFrom(this.api.startProcess(name)),
      `Started ${name}.`
    );
  }

  async stop(name: string) {
    await this.runAction(
      () => firstValueFrom(this.api.stopProcess(name)),
      `Stopped ${name}.`
    );
  }

  async restart(name: string) {
    await this.runAction(
      () => firstValueFrom(this.api.restartProcess(name)),
      `Restarted ${name}.`
    );
  }

  async toggle(symbol: string) {
    if (!this.status) return;
    const entry = this.status.telemetry[symbol];
    if (!entry) return;
    await this.runAction(
      () => firstValueFrom(this.api.toggleSymbol(symbol, !entry.enabled)),
      `${symbol} ${entry.enabled ? "disabled" : "enabled"}.`
    );
  }

  async runBacktest() {
    await this.runAction(() => firstValueFrom(this.api.runBacktest()), "Backtest state reset.");
  }

  async runTargetedBacktest() {
    const symbol = this.backtestSymbol.trim();
    if (!symbol) {
      this.actionError = "Select a symbol first.";
      return;
    }

    const strategyName = this.backtestStrategy.trim();
    const payload: BacktestRequestPayload = {
      symbol,
      strategy_name: strategyName || undefined,
    };

    this.persistBacktestSelection();
    const label = strategyName ? `${symbol}:${strategyName}` : symbol;
    await this.runAction(
      () => firstValueFrom(this.api.runBacktest(payload)),
      `Backtest queued for ${label}.`
    );
  }

  async compileStrategy() {
    this.compileStrategyPreview();
    const name = (this.strategyForm.name || "").trim() || "EMA_Proxim8te";
    this.actionError = null;
    this.actionStatus = `Compiled strategy config for ${name}.`;
  }

  async createStrategy() {
    this.compileStrategyPreview();
    const config = this.buildStrategyConfig();
    const name = String((config as Record<string, unknown>)["name"] || "").trim() || "EMA_Proxim8te";
    await this.runAction(async () => {
      const response = (await firstValueFrom(
        this.api.createStrategy({
          name,
          config,
          overwrite: true,
        })
      )) as StrategyCreateResponse;
      if (!response.ok) {
        throw new Error("Failed to persist strategy config.");
      }
    }, `Strategy saved: ${name}.`);
  }

  async reloadConfig() {
    await this.runAction(() => firstValueFrom(this.api.reloadConfig()), "Config reloaded.");
  }
}
