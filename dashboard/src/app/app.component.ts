import { CommonModule } from "@angular/common";
import { Component, OnDestroy, OnInit } from "@angular/core";
import { FormsModule } from "@angular/forms";
import { Subscription, timer, firstValueFrom } from "rxjs";
import { switchMap } from "rxjs/operators";
import { ApiService } from "./api.service";
import {
  AccountHistoryPoint,
  AccountHistoryResponse,
  AccountHistorySummary,
  StatusResponse,
  SupportArticle,
  SupportTicketPayload,
} from "./models";

type PanelKey = "account" | "symbols" | "bot" | "logs" | "store" | "support";

type SymbolFilter = "all" | "active" | "inactive";

type LogSeverity = "all" | "critical" | "warn" | "info";

interface IssueEntry {
  severity: "critical" | "warn" | "info";
  title: string;
  detail: string;
  time: string | null;
}

@Component({
  selector: "app-root",
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: "./app.component.html",
  styleUrls: ["./app.component.css"],
})
export class AppComponent implements OnInit, OnDestroy {
  status: StatusResponse | null = null;
  lastUpdated: string | null = null;
  loading = true;
  error: string | null = null;
  activePanel: PanelKey = "account";
  issues: IssueEntry[] = [];
  symbolQuery = "";
  symbolFilter: SymbolFilter = "all";
  logQuery = "";
  logSeverity: LogSeverity = "all";

  accountHistory: AccountHistoryResponse | null = null;
  accountSummary: AccountHistorySummary | null = null;
  equitySeries: AccountHistoryPoint[] = [];
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

  private statusSub: Subscription | null = null;
  private historySub: Subscription | null = null;

  constructor(private api: ApiService) {}

  ngOnInit(): void {
    this.statusSub = timer(0, 2000)
      .pipe(switchMap(() => this.api.getStatus()))
      .subscribe({
        next: (data) => {
          this.status = data;
          this.lastUpdated = data.timestamp;
          this.loading = false;
          this.error = null;
          this.updateIssues();
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
          this.equitySeries = [];
          this.equityPolyline = "";
          this.equityAreaPath = "";
        },
      });
  }

  ngOnDestroy(): void {
    this.statusSub?.unsubscribe();
    this.historySub?.unsubscribe();
  }

  processNames(): string[] {
    if (!this.status) return [];
    return Object.keys(this.status.processes);
  }

  symbolNames(): string[] {
    if (!this.status) return [];
    return Object.keys(this.status.telemetry);
  }

  filteredSymbols(): string[] {
    const base = this.symbolNames();
    const query = this.symbolQuery.trim().toLowerCase();
    return base.filter((symbol) => {
      const entry = this.status?.telemetry[symbol];
      if (this.symbolFilter === "active" && !entry?.enabled) return false;
      if (this.symbolFilter === "inactive" && entry?.enabled) return false;
      if (!query) return true;
      return symbol.toLowerCase().includes(query);
    });
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

  filteredIssues(): IssueEntry[] {
    const query = this.logQuery.trim().toLowerCase();
    return this.issues.filter((issue) => {
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
    this.equitySeries = data.points || [];
    this.buildEquityChart();
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
    await firstValueFrom(this.api.startProcess(name));
  }

  async stop(name: string) {
    await firstValueFrom(this.api.stopProcess(name));
  }

  async restart(name: string) {
    await firstValueFrom(this.api.restartProcess(name));
  }

  async toggle(symbol: string) {
    if (!this.status) return;
    const entry = this.status.telemetry[symbol];
    await firstValueFrom(this.api.toggleSymbol(symbol, !entry.enabled));
  }

  async runBacktest() {
    await firstValueFrom(this.api.runBacktest());
  }

  async reloadConfig() {
    await firstValueFrom(this.api.reloadConfig());
  }
}
