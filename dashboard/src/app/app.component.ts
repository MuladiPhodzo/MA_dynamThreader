import { CommonModule } from "@angular/common";
import { Component, OnDestroy, OnInit } from "@angular/core";
import { Subscription, timer, firstValueFrom } from "rxjs";
import { switchMap } from "rxjs/operators";
import { ApiService } from "./api.service";
import { StatusResponse } from "./models";

@Component({
  selector: "app-root",
  standalone: true,
  imports: [CommonModule],
  templateUrl: "./app.component.html",
  styleUrls: ["./app.component.css"],
})
export class AppComponent implements OnInit, OnDestroy {
  status: StatusResponse | null = null;
  lastUpdated: string | null = null;
  loading = true;
  error: string | null = null;

  private sub: Subscription | null = null;

  constructor(private api: ApiService) {}

  ngOnInit(): void {
    this.sub = timer(0, 2000)
      .pipe(switchMap(() => this.api.getStatus()))
      .subscribe({
        next: (data) => {
          this.status = data;
          this.lastUpdated = data.timestamp;
          this.loading = false;
          this.error = null;
        },
        error: (err) => {
          this.loading = false;
          this.error = err?.message || "Failed to load status";
        },
      });
  }

  ngOnDestroy(): void {
    this.sub?.unsubscribe();
  }

  processNames(): string[] {
    if (!this.status) return [];
    return Object.keys(this.status.processes);
  }

  symbolNames(): string[] {
    if (!this.status) return [];
    return Object.keys(this.status.telemetry);
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
