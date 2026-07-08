import axios, { AxiosError, type AxiosInstance, type AxiosRequestConfig } from 'axios';
import type { ErrorBody, ErrorResponse } from '@/types/api';

/**
 * API client config - defaults to backend on localhost:8666.
 */
export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ??
  'http://localhost:8666/api/v1';

export class ApiError extends Error {
  readonly code: string;
  readonly retryable: boolean;
  readonly status?: number;

  constructor(body: ErrorBody, status?: number) {
    super(body.message);
    this.name = 'ApiError';
    this.code = body.code;
    this.retryable = body.retryable;
    this.status = status;
  }
}

/**
 * Wrapper result: either `data` or `error` (the OpenAPI error envelope).
 * Throwing is for the truly unexpected (network down, parse failure).
 */
export type ApiResult<T> = { ok: true; data: T } | { ok: false; error: ErrorBody };

/**
 * Low-level ApiClient.  All hooks go through here.
 * - Normalizes error envelope to ApiError
 * - 30 s default timeout
 * - JSON in/out
 */
export class ApiClient {
  private http: AxiosInstance;

  constructor(baseURL: string = API_BASE_URL) {
    this.http = axios.create({
      baseURL,
      timeout: 30_000,
      headers: { 'Content-Type': 'application/json' },
    });

    // Response interceptor: unwrap {ok, data} → data; throw on {ok: false}
    this.http.interceptors.response.use((resp) => {
      const body = resp.data;
      // Pass through raw payloads (numbers, strings) and list envelopes
      if (body && typeof body === 'object' && 'ok' in body) {
        if (body.ok === false) {
          const err = body as ErrorResponse;
          throw new ApiError(err.error, resp.status);
        }
        if ('data' in body) {
          // Some endpoints wrap under "data" — preserve items/top-level for compatibility
          return { ...resp, data: body.data ?? body };
        }
      }
      return resp;
    });
  }

  get<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
    return this.http.get<T>(url, config).then((r) => r.data);
  }
  post<T>(url: string, body?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return this.http.post<T>(url, body, config).then((r) => r.data);
  }
  put<T>(url: string, body?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return this.http.put<T>(url, body, config).then((r) => r.data);
  }
  patch<T>(url: string, body?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return this.http.patch<T>(url, body, config).then((r) => r.data);
  }
  delete<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
    return this.http.delete<T>(url, config).then((r) => r.data);
  }
}

let _apiClient: ApiClient | null = null;

/**
 * Get the singleton API client.
 */
export function getApiClient(): ApiClient {
  if (_apiClient) return _apiClient;
  _apiClient = new ApiClient();
  return _apiClient;
}

/**
 * Backward-compat alias. In the original design this was a static instance;
 * keep the same name so existing imports work, but proxy to the lazy getter.
 */
export const apiClient: ApiClient = new Proxy({} as ApiClient, {
  get(_target, prop: string | symbol) {
    const client = getApiClient();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const value = (client as any)[prop];
    return typeof value === 'function' ? value.bind(client) : value;
  },
});

/**
 * Inspect any thrown value and return a friendly message.
 */
export function describeError(err: unknown): string {
  if (err instanceof ApiError) return `[${err.code}] ${err.message}`;
  if (err instanceof AxiosError) {
    if (err.code === 'ERR_NETWORK') return '无法连接到后端，请检查主控是否启动';
    if (err.response) return `HTTP ${err.response.status}: ${err.message}`;
    return err.message;
  }
  if (err instanceof Error) return err.message;
  return String(err);
}

export { AxiosError };
