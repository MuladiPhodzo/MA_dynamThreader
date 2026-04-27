import { Injectable } from "@angular/core";
import { HttpClient } from "@angular/common/http";
import { Observable } from "rxjs";
import {
  BacktestRequestPayload,
  AccountHistoryResponse,
  StatusResponse,
  StrategyRegistryListResponse,
  StrategyCreateRequestPayload,
  StrategyCreateResponse,
  StrategyCatalogResponse,
  SupportKbResponse,
  SupportTicketPayload,
  SupportTicketResponse,
} from "./models";

@Injectable({ providedIn: "root" })
export class ApiService {
  private baseUrl = "/api";

  constructor(private http: HttpClient) {}

  getStatus(): Observable<StatusResponse> {
    return this.http.get<StatusResponse>(`${this.baseUrl}/status`);
  }

  startProcess(name: string) {
    return this.http.post(`${this.baseUrl}/processes/${name}/start`, {});
  }

  stopProcess(name: string) {
    return this.http.post(`${this.baseUrl}/processes/${name}/stop`, {});
  }

  restartProcess(name: string) {
    return this.http.post(`${this.baseUrl}/processes/${name}/restart`, {});
  }

  toggleSymbol(symbol: string, enabled: boolean) {
    return this.http.post(`${this.baseUrl}/symbols/${symbol}/toggle`, { enabled });
  }

  reloadConfig() {
    return this.http.post(`${this.baseUrl}/config/reload`, {});
  }

  runBacktest(payload: BacktestRequestPayload = {}) {
    return this.http.post(`${this.baseUrl}/backtest/run`, payload);
  }

  createStrategy(payload: StrategyCreateRequestPayload) {
    return this.http.post<StrategyCreateResponse>(`${this.baseUrl}/strategy/create`, payload);
  }

  getStrategyList() {
    return this.http.get<StrategyRegistryListResponse>(`${this.baseUrl}/strategy/list`);
  }

  getStrategyCatalog() {
    return this.http.get<StrategyCatalogResponse>(`${this.baseUrl}/strategy/catalog`);
  }

  getAccountHistory() {
    return this.http.get<AccountHistoryResponse>(`${this.baseUrl}/account/history`);
  }

  createSupportTicket(payload: SupportTicketPayload) {
    return this.http.post<SupportTicketResponse>(`${this.baseUrl}/support/ticket`, payload);
  }

  getSupportKb() {
    return this.http.get<SupportKbResponse>(`${this.baseUrl}/support/kb`);
  }
}
