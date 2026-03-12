import { Injectable } from "@angular/core";
import { HttpClient } from "@angular/common/http";
import { Observable } from "rxjs";
import { StatusResponse } from "./models";

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

  runBacktest() {
    return this.http.post(`${this.baseUrl}/backtest/run`, {});
  }
}
